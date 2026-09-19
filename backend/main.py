"""Assistant UI Backend Application Composition Root.

Wires together FastAPI application, middlewares, domain APIRouters, lifespan/startup
workers, and re-exports symbols for zero-loss backwards compatibility.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional
import uuid
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

# Alias sys.modules entries so 'main' and 'backend.main' always share the exact same module
if "backend.main" in sys.modules and "main" not in sys.modules:
    sys.modules["main"] = sys.modules["backend.main"]
elif "main" in sys.modules and "backend.main" not in sys.modules:
    sys.modules["backend.main"] = sys.modules["main"]

from fastapi import FastAPI, HTTPException, Request, Response, Depends, Query, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Core leaf modules
from backend.core.config import *
from backend.core.constants import *
from backend.core.database import *
from backend.core.clients import *
from backend.core.state import *
from backend.core.utils import *

# Domain models & schemas
from backend.models import *
from backend.schemas import *

# Domain services
from backend.services import *
from backend.services import (
    auth_service,
    arrival_service,
    booking_service,
    sms_service,
    operations_service,
    knowledge_service,
    settings_service,
    learning_service,
    phone_service,
    notification_service,
    bootcamp_service,
)

# Curator
from backend.curator import *

# Knowledge & RAG
from backend.knowledge import (
    render_template_variables,
    set_style_examples_enabled,
    is_style_examples_enabled,
    set_example_index,
    get_example_index,
    STYLE_EXAMPLES_ENABLED,
    example_index,
    DATASET_FILE,
    SMSExampleIndex,
    CANONICAL_INTENT_TAXONOMY,
    CRITICAL_UNRENDERED_TOKENS,
    STRICT_PLACEHOLDER_ALLOWLIST,
    STYLE_STOP_WORDS,
    STYLE_TEMPLATE_FALLBACKS,
    TOKEN_RE,
    UNRESOLVED_PLACEHOLDER_PATTERNS,
    classify_query_intent,
    get_style_examples,
    render_style_examples,
    resolve_dataset_path,
    tokenise,
    validate_no_unresolved_placeholders,
    KnowledgeRecord,
    KnowledgeEvidence,
    KnowledgeAlias,
    KnowledgeRepository,
    MigrationStage,
    ParityVerificationReport,
    KnowledgeParityChecker,
    KnowledgeDualWriteManager,
    EmbeddingService,
    HybridKnowledgeRetriever,
    cosine_similarity,
    find_similar_knowledge_records,
)

# Domain APIRouters
try:
    from anon_content import router as anon_content_router
except ImportError:
    from backend.anon_content import router as anon_content_router

from backend.routes.auth import router as auth_router
from backend.routes.booking import router as booking_router
from backend.routes.arrival import router as arrival_router
from backend.routes.phone import router as phone_router
from backend.routes.sms import router as sms_router
from backend.routes.curator import router as curator_router
from backend.routes.settings import router as settings_router
from backend.routes.operations import router as operations_router
from backend.routes.bootcamp import router as bootcamp_router
from backend.routes.misc import router as misc_router
from backend.routes import *
from backend.services import *
try:
    from backend.agent_console import *
except ImportError:
    from agent_console import *
try:
    from backend.booking_tools import *
except ImportError:
    from booking_tools import *

@contextlib.asynccontextmanager
async def lifespan(application: FastAPI):
    """Unified application lifespan managing startup recovery and background workers."""
    # Additive schema creation keeps the durable ntfy dedupe ledger available
    # on existing Fly volumes without altering or dropping application data.
    Base.metadata.create_all(bind=engine)
    # Knowledge is persisted on the Fly volume but retrieved from this
    # process-local index. Rebuild it on every process start so a deployment
    # or restart cannot make approved knowledge appear to have disappeared.
    load_knowledge_base()
    # Run synchronous startup recovery
    recover_interrupted_agent_console_runs()

    # Launch background tasks
    tasks = [
        asyncio.create_task(arrival_alert_worker()),
        asyncio.create_task(start_agent_console_retention_worker()),
        asyncio.create_task(booking_reminder_worker()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


# Initialize FastAPI application
app = FastAPI(title="Assistant UI Backend", lifespan=lifespan)

# Include anonymous content router
app.include_router(anon_content_router)

# Include domain APIRouters
app.include_router(auth_router)
app.include_router(booking_router)
app.include_router(arrival_router)
app.include_router(phone_router)
app.include_router(sms_router)
app.include_router(curator_router)
app.include_router(settings_router)
app.include_router(operations_router)
app.include_router(bootcamp_router)

# Mount static frontend assets and SPA fallback router
frontend_dist = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))
if os.path.exists(frontend_dist):
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
app.include_router(misc_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_api_response_caching(request: Request, call_next):
    """Keep shared API state and stable live booking entry points fresh."""
    response = await call_next(request)
    stable_live_paths = {"/", "/landing.html", "/booking", "/booking-inline.js"} | PORTAL_SPA_PATHS
    if request.url.path.startswith("/api/") or request.url.path in stable_live_paths:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    if is_public_request(request):
        return await call_next(request)

    if not AUTH_PASSWORD:
        return await call_next(request)

    if _valid_admin_session(request.cookies.get(AUTH_COOKIE_NAME, "")):
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    scheme, _, encoded = authorization.partition(" ")
    supplied_username = ""
    supplied_password = ""

    if scheme.lower() == "basic" and encoded:
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            supplied_username, separator, supplied_password = decoded.partition(":")
            if not separator:
                supplied_username = ""
                supplied_password = ""
        except (ValueError, UnicodeDecodeError):
            pass

    if _valid_admin_credentials(supplied_username, supplied_password):
        response = await call_next(request)
        _set_admin_session_cookie(response, request)
        return response

    headers = {}
    if not request.url.path.startswith("/api/"):
        headers["WWW-Authenticate"] = 'Basic realm="Assistant UI", charset="UTF-8"'
    return Response(content="Authentication required.", status_code=401, headers=headers)



if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8025))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

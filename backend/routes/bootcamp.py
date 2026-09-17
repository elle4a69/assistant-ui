"""AI Bootcamp training, personas, and simulations routes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

try:
    from backend.core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE, STYLE_PROFILE_STORE
    from backend.core.clients import openai_client
    from backend.core.utils import _dyn
    from backend.bootcamp import (
        PERSONAS as BOOTCAMP_PERSONAS,
        DEFAULT_STYLE_PROFILE,
        BootcampRunner,
        normalize_style_profile,
        load_opening_messages,
    )
    from backend.schemas.domain import (
        BootcampProfileInput, BootcampProfileApplyInput,
        BootcampRunInput, BootcampControlInput,
        BootcampInformationRequestInput,
    )
    from backend.services.bootcamp_service import (
        generate_bootcamp_tori_reply,
        generate_bootcamp_information_resolution,
        generate_bootcamp_persona_reply,
    )
    from backend.services.learning_service import save_learned_information
except ImportError:
    from core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE, STYLE_PROFILE_STORE
    from core.clients import openai_client
    from core.utils import _dyn
    from bootcamp import (
        PERSONAS as BOOTCAMP_PERSONAS,
        DEFAULT_STYLE_PROFILE,
        BootcampRunner,
        normalize_style_profile,
        load_opening_messages,
    )
    from schemas.domain import (
        BootcampProfileInput, BootcampProfileApplyInput,
        BootcampRunInput, BootcampControlInput,
        BootcampInformationRequestInput,
    )
    from services.bootcamp_service import (
        generate_bootcamp_tori_reply,
        generate_bootcamp_information_resolution,
        generate_bootcamp_persona_reply,
    )
    from services.learning_service import save_learned_information

BOOTCAMP_RUNNER = BootcampRunner(
    store=BOOTCAMP_STORE,
    openings=load_opening_messages(BOOTCAMP_OPENINGS_FILE),
    generate_tori=generate_bootcamp_tori_reply,
    generate_persona=generate_bootcamp_persona_reply,
    max_workers=int(os.getenv("BOOTCAMP_MAX_WORKERS", "6")),
    message_delay_seconds=float(os.getenv("BOOTCAMP_MESSAGE_DELAY_SECONDS", "2.5")),
)

router = APIRouter()

@router.get("/api/bootcamp/personas")
def get_bootcamp_personas():
    return BOOTCAMP_PERSONAS


@router.get("/api/bootcamp/profile")
def get_bootcamp_profile():
    store = _dyn("STYLE_PROFILE_STORE", STYLE_PROFILE_STORE)
    return {
        "active": store.get_active(),
        "defaults": DEFAULT_STYLE_PROFILE,
        "isApplied": store.is_applied(),
        "canUndo": store.can_undo(),
    }


@router.post("/api/bootcamp/profile/apply")
def apply_bootcamp_profile(payload: BootcampProfileInput):
    store = _dyn("STYLE_PROFILE_STORE", STYLE_PROFILE_STORE)
    return {
        "active": store.apply(payload.styleProfile),
        "isApplied": True,
        "canUndo": True,
    }


@router.post("/api/bootcamp/profile/undo")
def undo_bootcamp_profile():
    store = _dyn("STYLE_PROFILE_STORE", STYLE_PROFILE_STORE)
    active = store.undo()
    return {
        "active": active,
        "isApplied": store.is_applied(),
        "canUndo": store.can_undo(),
    }


@router.post("/api/bootcamp/runs")
def start_bootcamp_run(payload: BootcampRunInput):
    client = _dyn("openai_client", openai_client)
    if not client:
        raise HTTPException(status_code=503, detail="OpenAI is not configured")
    if not payload.personaIds:
        raise HTTPException(status_code=400, detail="Select at least one persona")
    runner = _dyn("BOOTCAMP_RUNNER", BOOTCAMP_RUNNER)
    store = _dyn("BOOTCAMP_STORE", BOOTCAMP_STORE)
    run_id = runner.start(
        payload.personaIds,
        max(1, min(20, payload.maxTurns)),
        normalize_style_profile(payload.styleProfile),
    )
    return store.get_run(run_id)


@router.get("/api/bootcamp/runs/latest")
def get_latest_bootcamp_run():
    store = _dyn("BOOTCAMP_STORE", BOOTCAMP_STORE)
    return {"run": store.latest_run()}


@router.get("/api/bootcamp/runs/{run_id}")
def get_bootcamp_run(run_id: str):
    store = _dyn("BOOTCAMP_STORE", BOOTCAMP_STORE)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Boot Camp run not found")
    return run


@router.post("/api/bootcamp/conversations/{conversation_id}/information-request/respond")
def respond_to_bootcamp_information_request(
    conversation_id: str,
    payload: BootcampInformationRequestInput,
):
    store = _dyn("BOOTCAMP_STORE", BOOTCAMP_STORE)
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Boot Camp conversation not found.")
    if not conversation["needsHandoff"] or conversation["status"] != "handoff":
        raise HTTPException(status_code=409, detail="This Boot Camp information request is already resolved.")

    latest_persona_message = next(
        (message for message in reversed(conversation["messages"]) if message.get("role") == "persona"),
        None,
    )
    if not latest_persona_message:
        raise HTTPException(status_code=409, detail="No simulated customer message is available to retry.")

    gen_fn = _dyn("generate_bootcamp_information_resolution", generate_bootcamp_information_resolution)
    save_fn = _dyn("save_learned_information", save_learned_information)

    generated = gen_fn(
        conversation["messages"],
        conversation["styleProfile"],
        payload.information,
    )
    knowledge_entry_id = f"bootcamp-{conversation_id}-{latest_persona_message['id']}"
    knowledge_source = save_fn(
        knowledge_entry_id,
        latest_persona_message["text"],
        payload.information,
        generated["knowledge_summary"],
        "primary",
    )
    store.add_message(
        conversation_id,
        "tori",
        generated["customer_reply"],
        {
            "source": "information-request",
            "knowledgeSource": knowledge_source,
            "knowledgeSummary": generated["knowledge_summary"],
        },
    )
    store.resolve_handoff(conversation_id)
    return {
        "status": "success",
        "conversation": store.get_conversation(conversation_id),
        "knowledgeSource": knowledge_source,
        "knowledgeSummary": generated["knowledge_summary"],
    }


@router.post("/api/bootcamp/runs/{run_id}/control")
def control_bootcamp_run(run_id: str, payload: BootcampControlInput):
    store = _dyn("BOOTCAMP_STORE", BOOTCAMP_STORE)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Boot Camp run not found")
    operation = payload.operation.strip().lower()
    transitions = {"pause": "paused", "resume": "running", "stop": "stopped"}
    if operation not in transitions:
        raise HTTPException(status_code=400, detail="Use pause, resume, or stop")
    store.update_run(run_id, transitions[operation])
    return store.get_run(run_id)


@router.delete("/api/bootcamp/runs")
def reset_bootcamp_runs():
    store = _dyn("BOOTCAMP_STORE", BOOTCAMP_STORE)
    latest = store.latest_run()
    if latest and latest["status"] in {"running", "paused"}:
        raise HTTPException(status_code=409, detail="Stop the active run before resetting")
    store.reset()
    return {"status": "reset"}


__all__ = [
    "router",
    "get_bootcamp_personas",
    "get_bootcamp_profile",
    "apply_bootcamp_profile",
    "undo_bootcamp_profile",
    "start_bootcamp_run",
    "get_latest_bootcamp_run",
    "get_bootcamp_run",
    "respond_to_bootcamp_information_request",
    "control_bootcamp_run",
    "reset_bootcamp_runs",
]

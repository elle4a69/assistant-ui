"""Authentication and authority context domain service module.

Handles:
- Admin credentials verification
- Admin session token creation and verification
- Admin session cookie management
- Public route detection
- Authority context assembly
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import Request, Response

# Defensive dual-import fallbacks
try:
    from backend.core.config import (
        AUTH_COOKIE_NAME,
        AUTH_PASSWORD,
        AUTH_SESSION_MAX_AGE,
        AUTH_USERNAME,
        PORTAL_SPA_PATHS,
        PUBLIC_EXACT_PATHS,
    )
    from backend.core.clients import resolve_provider_context
except ImportError:
    from core.config import (
        AUTH_COOKIE_NAME,
        AUTH_PASSWORD,
        AUTH_SESSION_MAX_AGE,
        AUTH_USERNAME,
        PORTAL_SPA_PATHS,
        PUBLIC_EXACT_PATHS,
    )
    from core.clients import resolve_provider_context


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


def _valid_admin_credentials(username: str, password: str) -> bool:
    auth_password = _dyn("AUTH_PASSWORD", AUTH_PASSWORD)
    auth_username = _dyn("AUTH_USERNAME", AUTH_USERNAME)
    return bool(
        auth_password
        and hmac.compare_digest(username, auth_username)
        and hmac.compare_digest(password, auth_password)
    )


def _admin_session_token(expires_at: int) -> str:
    auth_password = _dyn("AUTH_PASSWORD", AUTH_PASSWORD)
    auth_username = _dyn("AUTH_USERNAME", AUTH_USERNAME)
    payload = f"{auth_username}:{expires_at}"
    signature = hmac.new(
        auth_password.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii")


def _valid_admin_session(token: str) -> bool:
    auth_password = _dyn("AUTH_PASSWORD", AUTH_PASSWORD)
    auth_username = _dyn("AUTH_USERNAME", AUTH_USERNAME)
    if not auth_password or not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires_text, signature = decoded.split(":", 2)
        expires_at = int(expires_text)
    except (ValueError, UnicodeDecodeError):
        return False
    if expires_at <= int(datetime.now(timezone.utc).timestamp()):
        return False
    payload = f"{username}:{expires_at}"
    expected = hmac.new(
        auth_password.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(username, auth_username) and hmac.compare_digest(signature, expected)


def _set_admin_session_cookie(response: Response, request: Request) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    secure = request.url.scheme == "https" or forwarded_proto.lower() == "https"
    max_age = _dyn("AUTH_SESSION_MAX_AGE", AUTH_SESSION_MAX_AGE)
    cookie_name = _dyn("AUTH_COOKIE_NAME", AUTH_COOKIE_NAME)
    expires_at = int(datetime.now(timezone.utc).timestamp()) + max_age
    response.set_cookie(
        cookie_name,
        _admin_session_token(expires_at),
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def is_public_request(request: Request) -> bool:
    """Keep the public website, booking widget, and required integrations open."""
    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()

    public_exact_paths = _dyn("PUBLIC_EXACT_PATHS", PUBLIC_EXACT_PATHS)
    portal_spa_paths = _dyn("PORTAL_SPA_PATHS", PORTAL_SPA_PATHS)

    if path in public_exact_paths:
        return True
    if method == "GET" and path in portal_spa_paths:
        return True
    if path == "/v2" or path.startswith("/v2/"):
        return True
    if path == "/anon":
        return True
    if path.startswith("/images/") or path.startswith("/assets/"):
        return True
    if path.startswith("/a/"):
        return True
    if path == "/api/arrival/activate" or path.startswith("/api/arrival/client/"):
        return True

    if method == "GET" and path in {"/api/anon/content", "/api/anon/image"}:
        return True

    # These three routes are the customer-facing booking widget API only.
    public_booking_api_paths = {
        "/api/services",
        "/api/calendar/freebusy",
        "/api/calendar/bookings",
    }
    if method == "OPTIONS" and path in public_booking_api_paths:
        return True
    if method == "GET" and path in {"/api/services", "/api/calendar/freebusy"}:
        return True
    if method == "POST" and path == "/api/calendar/bookings":
        return True

    return False


def build_authority_context(query: str, account_key: str, *, booking_or_availability: bool) -> str:
    """Assemble only the source classes permitted to influence a customer reply."""
    _res_provider = _dyn("resolve_provider_context", resolve_provider_context)
    _res_provider(account_key)
    if booking_or_availability:
        k_reason = _dyn("_knowledge_reason", None)
        if callable(k_reason):
            k_reason("knowledge_authority_overridden")
        get_live = _dyn("get_live_services_context", None)
        live_services = get_live(account_key) if callable(get_live) else ""
        return (
            "[Authority matrix]\n"
            "Availability and time slots: current live calendar only.\n"
            "Service names, prices and durations: current account Settings catalogue only.\n"
            "Durable knowledge is deliberately omitted for this booking or availability turn.\n\n"
            + live_services
        )
    build_biz = _dyn("build_business_context", None)
    business_context = ""
    if callable(build_biz):
        business_context = (
            build_biz(query)
            if account_key == "primary"
            else build_biz(query, account_key=account_key)
        )
    return "[Authority matrix]\nDurable policy and wording: approved active knowledge only.\n\n" + business_context


__all__ = [
    "_valid_admin_credentials",
    "_admin_session_token",
    "_valid_admin_session",
    "_set_admin_session_cookie",
    "is_public_request",
    "build_authority_context",
]

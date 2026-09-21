"""Authentication and health routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

try:
    from backend.core.config import AUTH_PASSWORD, AUTH_COOKIE_NAME
    from backend.schemas.domain import AdminLoginInput
    from backend.services.auth_service import (
        _set_admin_session_cookie,
        _valid_admin_credentials,
        _valid_admin_session,
    )
except ImportError:
    from core.config import AUTH_PASSWORD, AUTH_COOKIE_NAME
    from schemas.domain import AdminLoginInput
    from services.auth_service import (
        _set_admin_session_cookie,
        _valid_admin_credentials,
        _valid_admin_session,
    )

router = APIRouter()

@router.get("/api/health")
def health_check():
    """Public process-readiness response used by Fly and deployment monitoring."""
    return {"status": "ok", "service": "assistant-ui"}


@router.get("/api/auth/status")
def admin_auth_status(request: Request):
    if not AUTH_PASSWORD:
        return {"authenticated": True}
    return {"authenticated": _valid_admin_session(request.cookies.get(AUTH_COOKIE_NAME, ""))}


@router.post("/api/auth/login")
def admin_auth_login(payload: AdminLoginInput, request: Request, response: Response):
    if not _valid_admin_credentials(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    _set_admin_session_cookie(response, request)
    return {"authenticated": True}


@router.post("/api/auth/logout")
def admin_auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return {"authenticated": False}


__all__ = [
    "router",
    "health_check",
    "admin_auth_status",
    "admin_auth_login",
    "admin_auth_logout",
]

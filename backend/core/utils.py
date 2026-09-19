"""Neutral leaf utility functions for assistant-ui backend.

Contains pure datetime and string formatting helpers with zero dependencies
on database or route modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Optional

try:
    from backend.core.constants import URL_TRAILING_PUNCTUATION_RE
except ImportError:
    from core.constants import URL_TRAILING_PUNCTUATION_RE


def format_dt(dt: Optional[datetime]) -> Optional[str]:
    """Format a datetime to a UTC ISO string ending in 'Z'."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _normalise_url_for_comparison(url: str) -> str:
    """Strip trailing punctuation and slash, normalize case for URL comparison."""
    return URL_TRAILING_PUNCTUATION_RE.sub(r"\1", url).rstrip("/").lower()


def normalized_reply_fingerprint(text: str) -> str:
    """Normalized fingerprint of reply text for deduplication."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
    return re.sub(r"[^\w\s]", "", cleaned)


def _safe_csv_cell(value: object) -> str:
    """Prevent exported values from being interpreted as spreadsheet formulas."""
    text = "" if value is None else str(value)
    first_content_character = text.lstrip(" \t\r\n")[:1]
    if first_content_character in {"=", "+", "-", "@"}:
        return f"'{text}"
    return text


def sanitize_outgoing_urls(text: Optional[str]) -> Optional[str]:
    """Apply final SMS typography and URL safety rules."""
    if not text:
        return text
    text = re.sub(r"\s*—\s*", ", ", text).replace("–", "-")
    return URL_TRAILING_PUNCTUATION_RE.sub(r"\1", text)


def customer_explicitly_requests_link(message: str) -> bool:
    """Allow a repeat only when the customer has actually asked for one."""
    normalised = str(message or "").lower()
    return bool(re.search(
        r"\b(?:send|share|give|need|want|where(?:'s| is)|what(?:'s| is)).{0,40}\b(?:link|url|website|web\s*site|page)\b"
        r"|\b(?:link|url|website|web\s*site|page).{0,40}\b(?:again|please)\b",
        normalised,
    ))


def customer_explicitly_requests_payment_details(message: str) -> bool:
    return bool(re.search(
        r"\b(?:cash|deposit|payment|payid|bank\s*transfer|card|how\s+(?:do|can)\s+i\s+pay|payment\s+method)\b",
        str(message or ""),
        re.IGNORECASE,
    ))


def safe_exception_diagnostic(exc: BaseException) -> Dict[str, Any]:
    """Return content-free provider diagnostics suitable for persisted events."""
    cause = exc
    while cause.__cause__ is not None:
        cause = cause.__cause__
    diagnostic: Dict[str, Any] = {"exception_type": type(cause).__name__}
    status_code = getattr(cause, "status_code", None)
    if isinstance(status_code, int):
        diagnostic["provider_status_code"] = status_code
    error_code = getattr(cause, "code", None)
    if isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", error_code):
        diagnostic["provider_error_code"] = error_code
    return diagnostic


def _dyn(name: str, fallback: object = None) -> object:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    # Priority 1: Check if main or backend.main was monkeypatched (value differs from fallback)
    for mod_name in ("main", "backend.main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            val = getattr(mod, name)
            if fallback is not None and val is not fallback:
                return val
    # Priority 2: Return from main or backend.main if attribute exists
    for mod_name in ("main", "backend.main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    # Priority 3: Fallback if supplied
    if fallback is not None:
        return fallback
    # Priority 4: Search service modules
    for svc_mod_name in (
        "backend.services.booking_service",
        "services.booking_service",
        "backend.services.sms_service",
        "services.sms_service",
        "backend.services.auth_service",
        "services.auth_service",
        "backend.services.arrival_service",
        "services.arrival_service",
        "backend.services.operations_service",
        "services.operations_service",
        "backend.services.settings_service",
        "services.settings_service",
    ):
        mod = sys.modules.get(svc_mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


__all__ = [
    "format_dt",
    "_normalise_url_for_comparison",
    "normalized_reply_fingerprint",
    "_safe_csv_cell",
    "sanitize_outgoing_urls",
    "customer_explicitly_requests_link",
    "customer_explicitly_requests_payment_details",
    "safe_exception_diagnostic",
    "_dyn",
]

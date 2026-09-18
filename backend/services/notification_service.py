"""Central, best-effort ntfy notification delivery for operational alerts."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional
from urllib.parse import quote, urlencode

import requests
from sqlalchemy.exc import IntegrityError

try:
    from backend.core.database import SessionLocal
    from backend.models.domain import NotificationDelivery
except ImportError:
    from core.database import SessionLocal
    from models.domain import NotificationDelivery

logger = logging.getLogger(__name__)


def _dyn(name: str, fallback: Any = None) -> Any:
    """Keep this leaf service easily monkeypatchable through the composition root."""
    import sys
    for mod_name in ("backend.main", "main"):
        module = sys.modules.get(mod_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return fallback


def build_notification_url(path: str, **query: Optional[str]) -> str:
    """Build an app deep link without hard-coding a deployment origin."""
    safe_path = "/" + (path or "").lstrip("/")
    encoded_query = urlencode({key: value for key, value in query.items() if value is not None})
    target = f"{safe_path}?{encoded_query}" if encoded_query else safe_path
    origin = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
    return f"{origin}{target}" if origin else target


def _claim_delivery(dedupe_key: str, notification_type: str) -> bool:
    """Atomically claim a business event before external delivery.

    A failed ntfy call remains claimed deliberately. Retrying it from request or
    polling paths would otherwise produce notification storms and duplicate
    owner alerts.
    """
    session_factory = _dyn("SessionLocal", SessionLocal)
    db = session_factory()
    try:
        db.add(NotificationDelivery(
            dedupe_key=dedupe_key[:500],
            notification_type=(notification_type or "operational")[:100],
        ))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    except Exception:
        db.rollback()
        logger.warning("Could not claim ntfy notification delivery (type=%s)", notification_type or "operational")
        return False
    finally:
        db.close()


def send_notification(
    *,
    notification_type: str,
    title: str,
    message: str,
    click_url: str,
    priority: int = 4,
    metadata: Optional[Mapping[str, Any]] = None,
    dedupe_key: Optional[str] = None,
) -> bool:
    """Send one application notification through ntfy, safely and optionally once.

    ``metadata`` is reserved for callers and deliberately never sent or logged:
    it keeps domain-facing calls extensible without coupling them to ntfy.
    """
    del metadata
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return False

    if dedupe_key and not _claim_delivery(dedupe_key, notification_type):
        return False

    base_url = os.getenv("NTFY_BASE_URL", "https://ntfy.sh").strip().rstrip("/") or "https://ntfy.sh"
    try:
        normalised_priority = max(1, min(5, int(priority)))
    except (TypeError, ValueError):
        normalised_priority = 4
    headers = {
        "Title": str(title or "Assistant UI"),
        "Priority": str(normalised_priority),
        "Click": str(click_url or ""),
    }
    token = os.getenv("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(
            f"{base_url}/{quote(topic, safe='')}",
            data=str(message or "").encode("utf-8"),
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        return True
    except Exception:
        # Do not log request headers, body, URL query parameters, or an exception
        # repr: any of them can carry customer data or auth details.
        logger.warning("ntfy notification delivery failed (type=%s)", notification_type or "operational")
        return False


def send_thread_attention_notification(
    *,
    thread_id: str,
    sms_account_key: str,
    reason: str,
    source_key: str,
) -> bool:
    """Send one owner alert when a conversation newly needs human attention."""
    line_label = "Line 2" if sms_account_key == "secondary" else "Line 1"
    return send_notification(
        notification_type="sms_needs_review",
        title="SMS Needs Review",
        message=f"{line_label} · {str(reason or 'Human attention required.')[:180]}",
        click_url=build_notification_url("/chat", thread=thread_id),
        priority=4,
        dedupe_key=f"thread-review:{thread_id}:{source_key}",
        metadata={"thread_id": thread_id},
    )


def send_ntfy_notification(
    *,
    title: str,
    message: str,
    click_url: str,
    priority: int = 4,
) -> bool:
    """Compatibility wrapper for the verified incoming-SMS integration."""
    return send_notification(
        notification_type="incoming_sms",
        title=title,
        message=message,
        click_url=click_url,
        priority=priority,
    )


__all__ = [
    "build_notification_url", "send_notification", "send_thread_attention_notification",
    "send_ntfy_notification",
]

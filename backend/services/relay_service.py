"""Outbound SMS relay service.

Handles:
- Android Gateway wake-up doorbell trigger (with 10s debounce)
- Stranded outbound queue monitor worker (every 15s)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from backend.core.database import SessionLocal
    from backend.core.utils import _dyn
    from backend.models.domain import OutboundQueueMessage
except ImportError:
    from core.database import SessionLocal
    from core.utils import _dyn
    from models.domain import OutboundQueueMessage


# Timestamp of the last gateway wake-up trigger
last_wakeup_sent_at: Optional[datetime] = None
_wakeup_lock = threading.Lock()


def _fire_http_ping(url: str) -> None:
    """Non-blocking HTTP request to ping the gateway wakeup URL."""
    try:
        parsed = urllib.parse.urlsplit(url)
        username = parsed.username or os.getenv("ANDROID_GATEWAY_WAKEUP_USER")
        password = parsed.password or os.getenv("ANDROID_GATEWAY_WAKEUP_PASSWORD")

        # Strip credentials from netloc for clean request URL
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc += f":{parsed.port}"
            target_url = urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        else:
            target_url = url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AssistantUI-Relay-Doorbell/1.0",
        }

        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        elif username:
            token = base64.b64encode(f"{username}:".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        phone = os.getenv("ANDROID_GATEWAY_WAKEUP_PHONE", "").strip()
        path = parsed.path.rstrip("/")
        if phone or path.endswith("/message"):
            target_phone = phone if phone else "+61432172148"
            msg = os.getenv("ANDROID_GATEWAY_WAKEUP_MESSAGE", "DOORBELL PING from Android Gateway")
            payload = {
                "message": msg,
                "phoneNumbers": [target_phone],
            }
            body = json.dumps(payload).encode("utf-8")
        else:
            body = b"{}"

        req = urllib.request.Request(
            target_url,
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info("Gateway wake-up response: %s", resp.status)
    except urllib.error.HTTPError as e:
        if e.code == 405:
            try:
                get_headers = {"User-Agent": "AssistantUI-Relay-Doorbell/1.0"}
                if "Authorization" in headers:
                    get_headers["Authorization"] = headers["Authorization"]
                get_req = urllib.request.Request(target_url, headers=get_headers)
                with urllib.request.urlopen(get_req, timeout=5) as resp:
                    logger.info("Gateway wake-up GET fallback response: %s", resp.status)
            except Exception as inner_e:
                logger.warning("Gateway wake-up GET fallback failed: %s", inner_e)
        else:
            logger.warning("Gateway wake-up HTTP error: %s", e)
    except Exception as e:
        logger.warning("Gateway wake-up ping failed: %s", e)


def trigger_gateway_wakeup(force: bool = False) -> bool:
    """Trigger the Android Gateway wake-up doorbell.

    If not forced, triggers are debounced to at most once every 10 seconds.
    """
    global last_wakeup_sent_at
    now = datetime.utcnow()

    with _wakeup_lock:
        if not force and last_wakeup_sent_at is not None:
            elapsed = (now - last_wakeup_sent_at).total_seconds()
            if elapsed < 10.0:
                logger.debug("Gateway wake-up debounced (elapsed %.1fs < 10s)", elapsed)
                return False

        last_wakeup_sent_at = now

    url = os.getenv("ANDROID_GATEWAY_WAKEUP_URL", "").strip()
    if url:
        logger.info("Triggering Android gateway wake-up at %s (force=%s)", url, force)
        threading.Thread(target=_fire_http_ping, args=(url,), daemon=True).start()
        return True

    logger.debug("No ANDROID_GATEWAY_WAKEUP_URL configured; skipping wake-up ping.")
    return False


async def start_stranded_relay_worker() -> None:
    """Background worker that runs every 15s and forces wake-up if messages are stranded."""
    logger.info("Stranded relay worker started.")
    while True:
        try:
            await asyncio.sleep(15)
            session_factory = _dyn("SessionLocal", SessionLocal)
            db = session_factory()
            try:
                cutoff = datetime.utcnow() - timedelta(seconds=15)
                outbound_cls = _dyn("OutboundQueueMessage", OutboundQueueMessage)
                stranded_count = (
                    db.query(outbound_cls)
                    .filter(
                        outbound_cls.status == "pending",
                        outbound_cls.created_at <= cutoff,
                    )
                    .count()
                )
                if stranded_count > 0:
                    logger.warning(
                        "Found %d stranded pending outbound message(s); forcing gateway wake-up.",
                        stranded_count,
                    )
                    trigger_gateway_wakeup(force=True)
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.info("Stranded relay worker cancelled.")
            break
        except Exception as e:
            logger.error("Error in stranded relay worker: %s", e, exc_info=True)


__all__ = [
    "last_wakeup_sent_at",
    "trigger_gateway_wakeup",
    "start_stranded_relay_worker",
]

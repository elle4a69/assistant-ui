"""Best-effort ntfy notification delivery for operational alerts."""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


def send_ntfy_notification(
    *,
    title: str,
    message: str,
    click_url: str,
    priority: int = 4,
) -> bool:
    """Publish one notification to the configured ntfy topic.

    Configuration:
      NTFY_TOPIC      Required. Topic name to publish to.
      NTFY_BASE_URL   Optional. Defaults to https://ntfy.sh.
      NTFY_TOKEN      Optional bearer token for protected/self-hosted topics.

    Delivery is intentionally best-effort. A notification failure must never
    interrupt inbound SMS processing.
    """
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return False

    base_url = os.getenv("NTFY_BASE_URL", "https://ntfy.sh").strip().rstrip("/")
    if not base_url:
        base_url = "https://ntfy.sh"

    headers = {
        "Title": str(title or "Assistant UI"),
        "Priority": str(max(1, min(5, int(priority)))),
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
        logger.exception("ntfy notification delivery failed")
        return False

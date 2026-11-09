"""SMS webhook, simulator, and confirmation settings routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

try:
    from backend.core.config import PROMPTS_DIR
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service, canonical_phone_number
    from backend.core.utils import _dyn
    from backend.models.domain import Thread, Message, OutboundQueueMessage
    from backend.schemas.domain import (
        WebhookSMSInput, AdminSmsSimulationInput, SmsConfirmationInput,
    )
    from backend.services.arrival_service import normalize_simulator_customer_phone
    from backend.services.sms_service import (
        process_inbound_sms,
        run_sms_reply_logic,
    )
except ImportError:
    from core.config import PROMPTS_DIR
    from core.database import get_db
    from core.clients import mobilemessage_service, canonical_phone_number
    from core.utils import _dyn
    from models.domain import Thread, Message, OutboundQueueMessage
    from schemas.domain import (
        WebhookSMSInput, AdminSmsSimulationInput, SmsConfirmationInput,
    )
    from services.arrival_service import normalize_simulator_customer_phone
    from services.sms_service import (
        process_inbound_sms,
        run_sms_reply_logic,
    )

router = APIRouter()

@router.post("/webhooks/sms")
def webhook_sms(payload: WebhookSMSInput, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    ms = _dyn("mobilemessage_service", mobilemessage_service)
    supplied_destination = (payload.to or "").strip()
    matched_account = ms.matched_account_key_for_inbound_number(supplied_destination)
    if not supplied_destination or not matched_account:
        print("[Webhook Rejected] Inbound destination is not assigned to an enabled SMS account.")
        raise HTTPException(status_code=422, detail="Inbound SMS destination is not configured.")
    # An inbound message can enter only through an explicitly configured line.
    # Missing, malformed, or unknown destinations must never default to primary.
    process_fn = _dyn("process_inbound_sms", process_inbound_sms)
    return process_fn(payload, background_tasks, db, matched_account)




IPHONE_SENDER_FIELDS = ("sender", "from", "phone", "from_phone", "contact", "source")
IPHONE_BODY_FIELDS = ("message", "body", "text", "msg", "content")


@router.post("/webhook/inbound")
@router.post("/weebhook/inbound")
@router.post("/weebhook")
@router.post("/webhook")
async def iphone_shortcuts_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    content_type = request.headers.get("content-type", "").lower()
    data: Dict[str, Any] = {}
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            data = {}
    else:
        try:
            data = await request.json()
        except Exception:
            try:
                form = await request.form()
                data = dict(form)
            except Exception:
                data = {}

    if not isinstance(data, dict):
        data = {}

    raw_sender: Optional[str] = None
    for key in IPHONE_SENDER_FIELDS:
        val = data.get(key)
        if val is not None and str(val).strip():
            raw_sender = str(val).strip()
            break

    raw_body: Optional[str] = None
    for key in IPHONE_BODY_FIELDS:
        val = data.get(key)
        if val is not None and str(val).strip():
            raw_body = str(val).strip()
            break

    if not raw_sender or not raw_body:
        raise HTTPException(
            status_code=422,
            detail="Missing or empty sender phone number or message body.",
        )

    canonical_fn = _dyn("canonical_phone_number", canonical_phone_number)
    from_phone = canonical_fn(raw_sender)
    if not from_phone:
        raise HTTPException(
            status_code=422,
            detail="Invalid sender phone number.",
        )

    payload = WebhookSMSInput.model_validate({
        "from": from_phone,
        "body": raw_body,
        "to": "primary",
        "receivedAt": datetime.now(timezone.utc),
        "webhookType": "iphone_shortcuts",
    })

    process_fn = _dyn("process_inbound_sms", process_inbound_sms)
    result = process_fn(payload, background_tasks, db, sms_account_key="primary")

    thread_id = result.get("thread_id") if isinstance(result, dict) else None
    return {
        "status": "success",
        "thread_id": thread_id,
    }

IPHONE_OUTBOUND_PHONE_FIELDS = ("phone", "to", "recipient", "customer_phone", "sender", "destination")
IPHONE_OUTBOUND_MESSAGE_FIELDS = ("message", "reply", "body", "text", "msg", "content")


def _dequeue_oldest_outbound_message(db: Session) -> Dict[str, Any]:
    outbound_cls = _dyn("OutboundQueueMessage", OutboundQueueMessage)
    oldest = (
        db.query(outbound_cls)
        .filter(outbound_cls.status == "pending")
        .order_by(outbound_cls.created_at.asc())
        .first()
    )
    if not oldest:
        return {"has_message": "no"}

    oldest.status = "dispatched"
    oldest.dispatched_at = datetime.utcnow()
    db.commit()
    return {
        "has_message": "yes",
        "phone": oldest.phone,
        "reply": oldest.message,
        "message": oldest.message,
    }


@router.get("/webhook/outbound")
@router.get("/weebhook/outbound")
def get_iphone_shortcuts_outbound(db: Session = Depends(get_db)):
    return _dequeue_oldest_outbound_message(db)


@router.post("/webhook/outbound")
@router.post("/weebhook/outbound")
async def post_iphone_shortcuts_outbound(
    request: Request,
    db: Session = Depends(get_db),
):
    content_type = request.headers.get("content-type", "").lower()
    data: Dict[str, Any] = {}
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            data = {}
    else:
        try:
            data = await request.json()
        except Exception:
            try:
                form = await request.form()
                data = dict(form)
            except Exception:
                data = {}

    if not isinstance(data, dict):
        data = {}

    raw_phone: Optional[str] = None
    for key in IPHONE_OUTBOUND_PHONE_FIELDS:
        val = data.get(key)
        if val is not None and str(val).strip():
            raw_phone = str(val).strip()
            break

    raw_msg: Optional[str] = None
    for key in IPHONE_OUTBOUND_MESSAGE_FIELDS:
        val = data.get(key)
        if val is not None and str(val).strip():
            raw_msg = str(val).strip()
            break

    # If payload provided with both phone and message -> Enqueue
    if raw_phone and raw_msg:
        canonical_fn = _dyn("canonical_phone_number", canonical_phone_number)
        phone = canonical_fn(raw_phone) or raw_phone
        outbound_cls = _dyn("OutboundQueueMessage", OutboundQueueMessage)
        new_msg = outbound_cls(
            id=str(uuid.uuid4()),
            phone=phone,
            message=raw_msg,
            status="pending",
            created_at=datetime.utcnow(),
        )
        db.add(new_msg)
        db.commit()
        db.refresh(new_msg)
        return {
            "status": "queued",
            "id": new_msg.id,
        }

    # Otherwise called without payload (polling for pending messages) -> Dequeue
    return _dequeue_oldest_outbound_message(db)


@router.post("/api/admin/sms-simulator")
def simulate_inbound_sms(
    simulation: AdminSmsSimulationInput,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Run an inbound SMS through the app without contacting the SMS provider."""
    customer_phone = normalize_simulator_customer_phone(simulation.customer_phone)
    if not simulation.body.strip():
        raise HTTPException(status_code=422, detail="Message body must not be empty.")

    payload = WebhookSMSInput.model_validate({
        "from": customer_phone,
        "body": simulation.body.strip(),
        "receivedAt": datetime.now(timezone.utc),
        "isSimulation": True,
    })
    try:
        process_fn = _dyn("process_inbound_sms", process_inbound_sms)
        result = process_fn(
            payload,
            background_tasks,
            db,
            simulation.sms_account_key,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Admin SMS simulation failed")
        # This endpoint is admin-only. Return actionable exception detail while
        # stripping common credential-bearing URL components and long tokens.
        safe_detail = re.sub(r"(?i)(api[_-]?key|token|password|secret)=([^&\s]+)", r"\1=[redacted]", str(exc))
        safe_detail = re.sub(r"\b[A-Za-z0-9_-]{40,}\b", "[redacted]", safe_detail)
        raise HTTPException(
            status_code=500,
            detail=f"SMS simulation failed: {safe_detail or type(exc).__name__}",
        ) from exc

    return {
        **result,
        "customer_phone": customer_phone,
        "sms_account_key": simulation.sms_account_key,
        "provider_sends": 0,
    }


@router.get("/api/settings/sms-confirmation")
def get_sms_confirmation():
    template_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt")
    template = "Hi {name}, your booking for {service} on {time} is confirmed! See you then. - Tori"
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
        except Exception:
            pass
    return {"template": template}


@router.post("/api/settings/sms-confirmation")
def save_sms_confirmation(payload: SmsConfirmationInput):
    template_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt")
    try:
        os.makedirs(os.path.dirname(template_path), exist_ok=True)
        with open(template_path, "w", encoding="utf-8") as f:
            f.write(payload.template)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save SMS confirmation template: {e}")


__all__ = [
    "router",
    "webhook_sms",
    "iphone_shortcuts_webhook",
    "get_iphone_shortcuts_outbound",
    "post_iphone_shortcuts_outbound",
    "simulate_inbound_sms",
    "get_sms_confirmation",
    "save_sms_confirmation",
]

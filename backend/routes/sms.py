"""SMS webhook, simulator, and confirmation settings routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

try:
    from backend.core.config import PROMPTS_DIR
    from backend.core.database import get_db, init_db
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
    from backend.services.relay_service import trigger_gateway_wakeup
except ImportError:
    from core.config import PROMPTS_DIR
    from core.database import get_db, init_db
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
    try:
        from backend.services.relay_service import trigger_gateway_wakeup
    except ImportError:
        from services.relay_service import trigger_gateway_wakeup

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


@router.get("/webhook/inbound")
@router.get("/weebhook/inbound")
@router.get("/webhook")
@router.get("/weebhook")
def get_iphone_shortcuts_inbound_info():
    return {
        "status": "ok",
        "endpoint": "inbound_sms_webhook",
        "supported_methods": ["POST"],
        "message": "Inbound SMS webhook is active. Post JSON or form data with sender/message.",
    }


_inbound_dedupe_cache: Dict[Tuple[str, str], float] = {}
_inbound_dedupe_lock = threading.Lock()


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

    inner = data.get("payload")
    if isinstance(inner, dict):
        merged = dict(data)
        merged.update(inner)
        data = merged

    lookup = {str(k).lower(): v for k, v in data.items()}

    event_val = lookup.get("event")
    if event_val and event_val != "sms:received":
        return {"status": "ignored", "event": event_val}

    raw_sender: Optional[str] = None
    for key in IPHONE_SENDER_FIELDS:
        val = lookup.get(key)
        if val is not None and str(val).strip():
            raw_sender = str(val).strip()
            break

    raw_body: Optional[str] = None
    for key in IPHONE_BODY_FIELDS:
        val = lookup.get(key)
        if val is not None and str(val).strip():
            raw_body = str(val).strip()
            break

    if not raw_sender or not raw_body:
        raise HTTPException(
            status_code=422,
            detail="Missing or empty sender phone number or message body.",
        )

    # Filter out Doorbell wake-up ping SMS from Central Gateway
    if "doorbell" in raw_body.lower():
        logger.info("[Doorbell Ping Filtered] Acknowledging gateway wake-up text without creating customer thread.")
        print("[Doorbell Ping Filtered] Acknowledging gateway wake-up text without creating customer thread.", flush=True)
        return {"status": "success", "event": "doorbell_ack"}

    canonical_fn = _dyn("canonical_phone_number", canonical_phone_number)
    from_phone = canonical_fn(raw_sender)
    if not from_phone:
        raise HTTPException(
            status_code=422,
            detail="Invalid sender phone number.",
        )

    client_token = _extract_client_token(data, request)

    # 15-second sliding window deduplication for identical sender + message body
    now_ts = time.time()
    is_duplicate = False
    existing_thread_id = None

    with _inbound_dedupe_lock:
        expired = [k for k, ts in _inbound_dedupe_cache.items() if now_ts - ts > 60.0]
        for k in expired:
            _inbound_dedupe_cache.pop(k, None)

        cache_key = (from_phone, raw_body.strip())
        last_seen = _inbound_dedupe_cache.get(cache_key)
        if last_seen is not None and (now_ts - last_seen) < 15.0:
            is_duplicate = True
        else:
            _inbound_dedupe_cache[cache_key] = now_ts

    thread_cls = _dyn("Thread", Thread)
    existing_thread = db.query(thread_cls).filter(thread_cls.customer_phone == from_phone).first()
    if existing_thread:
        existing_thread_id = existing_thread.id

    if not is_duplicate and existing_thread:
        cutoff = datetime.utcnow() - timedelta(seconds=15)
        message_cls = _dyn("Message", Message)
        existing_recent_msg = (
            db.query(message_cls)
            .filter(
                message_cls.thread_id == existing_thread.id,
                message_cls.role == "customer",
                message_cls.text == raw_body,
                message_cls.at >= cutoff,
            )
            .first()
        )
        if existing_recent_msg:
            is_duplicate = True

    if is_duplicate:
        log_msg = f"[iPhone Inbound Deduplicated] Ignoring duplicate trigger for from_phone={from_phone} (received within dedupe window)"
        logger.info("%s", log_msg)
        print(log_msg, flush=True)
        resp = {
            "status": "success",
            "thread_id": existing_thread_id,
            "duplicate": True,
        }
        if client_token:
            resp["token"] = client_token
        return resp

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
    client_token = _extract_client_token(data, request)
    resp = {
        "status": "success",
        "thread_id": thread_id,
    }
    if client_token:
        resp["token"] = client_token
    return resp

IPHONE_OUTBOUND_PHONE_FIELDS = ("phone", "to", "recipient", "customer_phone", "sender", "destination")
IPHONE_OUTBOUND_MESSAGE_FIELDS = ("message", "reply", "body", "text", "msg", "content")
IPHONE_OUTBOUND_IMAGE_FIELDS = ("image_url", "media_url", "attachment_url", "image")
IPHONE_CLIENT_ID_FIELDS = ("token", "client_id", "clientId", "client", "tenant_id", "tenant")


def _extract_client_token(data: Dict[str, Any], request: Optional[Request] = None) -> Optional[str]:
    """Extract client/tenant token from body, query parameters, or HTTP headers."""
    for key in IPHONE_CLIENT_ID_FIELDS:
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    if request is not None:
        for qkey in IPHONE_CLIENT_ID_FIELDS:
            val = request.query_params.get(qkey)
            if val is not None and str(val).strip():
                return str(val).strip()
        for hkey in ("x-client-id", "x-client-token", "x-token", "x-tenant-id"):
            val = request.headers.get(hkey)
            if val is not None and str(val).strip():
                return str(val).strip()
    return None


def _dequeue_pending_outbound_messages(db: Session, client_id: Optional[str] = None) -> Dict[str, Any]:
    outbound_cls = _dyn("OutboundQueueMessage", OutboundQueueMessage)
    query = (
        db.query(outbound_cls)
        .filter(outbound_cls.status == "pending")
        .order_by(outbound_cls.created_at.asc())
    )
    if client_id:
        query = query.filter(outbound_cls.client_id == client_id)
    else:
        # General pending queue: messages without a specific tenant client_id
        query = query.filter(outbound_cls.client_id.is_(None))

    try:
        try:
            pending_rows = query.with_for_update(skip_locked=True).all()
        except Exception:
            db.rollback()
            pending_rows = query.all()
    except Exception:
        # Gracefully handle missing client_id column during cold startup
        db.rollback()
        init_db_fn = _dyn("init_db", init_db)
        if callable(init_db_fn):
            try:
                init_db_fn()
            except Exception:
                pass
        try:
            pending_rows = query.all()
        except Exception:
            try:
                fallback_query = (
                    db.query(outbound_cls)
                    .filter(outbound_cls.status == "pending")
                    .order_by(outbound_cls.created_at.asc())
                )
                pending_rows = fallback_query.all()
            except Exception:
                pending_rows = []

    # Resilient fallback: if no untokened messages are found, fall back to the oldest
    # pending messages so an untokened query NEVER leaves messages stranded!
    if not pending_rows and not client_id:
        try:
            fallback_query = (
                db.query(outbound_cls)
                .filter(outbound_cls.status == "pending")
                .order_by(outbound_cls.created_at.asc())
            )
            try:
                pending_rows = fallback_query.with_for_update(skip_locked=True).all()
            except Exception:
                db.rollback()
                pending_rows = fallback_query.all()
        except Exception:
            pass

    if not pending_rows:
        return {
            "has_message": "no",
            "messages": [],
        }

    now = datetime.utcnow()
    for r in pending_rows:
        r.status = "dispatched"
        r.dispatched_at = now
    db.commit()

    first = pending_rows[0]
    return {
        "has_message": "yes",
        "messages": [
            {
                "id": r.id,
                "phone": r.phone,
                "reply": r.message,
                "image_url": r.image_url or "",
                "client_id": getattr(r, "client_id", "") or "",
            }
            for r in pending_rows
        ],
        "id": first.id,
        "phone": first.phone,
        "reply": first.message,
        "message": first.message,
        "image_url": first.image_url or "",
        "client_id": getattr(first, "client_id", "") or "",
    }


@router.get("/api/v1/relay/sms/pending")
@router.get("/webhook/outbound")
@router.get("/weebhook/outbound")
def get_iphone_shortcuts_outbound(
    request: Request,
    token: Optional[str] = None,
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    effective_client_id = _extract_client_token({"token": token, "client_id": client_id}, request)
    return _dequeue_pending_outbound_messages(db, client_id=effective_client_id)


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

    effective_client_id = _extract_client_token(data, request)

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

    raw_image: Optional[str] = None
    for key in IPHONE_OUTBOUND_IMAGE_FIELDS:
        val = data.get(key)
        if val is not None and str(val).strip():
            raw_image = str(val).strip()
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
            image_url=raw_image,
            client_id=effective_client_id,
            status="pending",
            created_at=datetime.utcnow(),
        )
        db.add(new_msg)
        db.commit()
        db.refresh(new_msg)

        wakeup_fn = _dyn("trigger_gateway_wakeup", trigger_gateway_wakeup)
        if callable(wakeup_fn):
            try:
                wakeup_fn()
            except Exception:
                pass

        resp = {
            "status": "queued",
            "id": new_msg.id,
        }
        if effective_client_id:
            resp["client_id"] = effective_client_id
        return resp

    # Otherwise called without payload (polling for pending messages) -> Dequeue
    return _dequeue_pending_outbound_messages(db, client_id=effective_client_id)


@router.get("/api/v1/relay/sms/sent")
@router.get("/api/v1/relay/sms/ack")
@router.get("/webhook/sent")
@router.get("/weebhook/sent")
def get_delivery_receipt_info():
    return {
        "status": "ok",
        "endpoint": "delivery_receipt",
        "supported_methods": ["POST"],
    }


@router.post("/api/v1/relay/sms/sent")
@router.post("/api/v1/relay/sms/ack")
@router.post("/webhook/sent")
@router.post("/weebhook/sent")
async def post_delivery_receipt(
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

    token = _extract_client_token(data, request)

    raw_ids = []
    if "ids" in data:
        val = data["ids"]
        if isinstance(val, list):
            raw_ids.extend(str(x).strip() for x in val if str(x).strip())
        elif isinstance(val, str):
            raw_ids.extend(part.strip() for part in val.split(",") if part.strip())
    if "id" in data and str(data["id"]).strip():
        for part in str(data["id"]).split(","):
            if part.strip():
                raw_ids.append(part.strip())
    if "message_id" in data and str(data["message_id"]).strip():
        for part in str(data["message_id"]).split(","):
            if part.strip():
                raw_ids.append(part.strip())

    target_ids = list(dict.fromkeys(raw_ids))
    outbound_cls = _dyn("OutboundQueueMessage", OutboundQueueMessage)
    now = datetime.utcnow()
    delivered_ids = []
    if target_ids:
        records = db.query(outbound_cls).filter(outbound_cls.id.in_(target_ids)).all()
        for rec in records:
            rec.status = "delivered"
            rec.delivered_at = now
            delivered_ids.append(rec.id)
        db.commit()

    if token:
        logger.info("Delivery receipt ack for token=%s, ids=%s", token, delivered_ids)
        print(f"[{now.isoformat()}] [DELIVERY_ACK][token={token}] ids={delivered_ids}", flush=True)

    resp = {
        "status": "success",
        "delivered_count": len(delivered_ids),
        "ids": delivered_ids,
    }
    if token:
        resp["token"] = token
    return resp


@router.get("/api/v1/relay/log")
@router.get("/webhook/log")
@router.get("/weebhook/log")
def get_webhook_log_info():
    return {
        "status": "ok",
        "endpoint": "shortcut_telemetry",
        "supported_methods": ["POST"],
    }


@router.post("/api/v1/relay/log")
@router.post("/webhook/log")
@router.post("/weebhook/log")
async def post_shortcut_telemetry_log(request: Request):
    ts = datetime.utcnow().isoformat()
    raw_bytes = await request.body()
    payload = None
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        payload = raw_bytes.decode("utf-8", errors="replace")

    token = None
    if isinstance(payload, dict):
        for k in ("token", "client_id"):
            if k in payload and isinstance(payload[k], str):
                payload[k] = payload[k].strip()
        token = _extract_client_token(payload, request)
    else:
        token = _extract_client_token({}, request)

    token_prefix = f"[token={token}]" if token else ""
    log_msg = f"[{ts}] [SHORTCUT_TELEMETRY]{token_prefix} {json.dumps(payload) if isinstance(payload, (dict, list)) else payload}"
    print(log_msg, flush=True)
    logger.info("%s", log_msg)

    resp = {
        "status": "logged",
        "timestamp": ts,
    }
    if token:
        resp["token"] = token
    return resp


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
    "get_iphone_shortcuts_inbound_info",
    "get_iphone_shortcuts_outbound",
    "post_iphone_shortcuts_outbound",
    "get_delivery_receipt_info",
    "post_delivery_receipt",
    "get_webhook_log_info",
    "post_shortcut_telemetry_log",
    "simulate_inbound_sms",
    "get_sms_confirmation",
    "save_sms_confirmation",
]

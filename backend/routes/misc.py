"""Miscellaneous routes: push notifications, shortlinks, RAG status, QA rules, draft messages, and SPA serving."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        BASE_DIR, DATA_DIR, STYLE_PROFILE_STORE, PROMPTS_DIR, FIRST_CONTACT_ACCOUNT_KEYS,
        AUTO_REPLY_GLOBAL_ENABLED,
    )
    from backend.core.constants import DAY_NAMES
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service, calendar_service, openai_client
    from backend.core.state import _vapid_key_lock, OUTBOUND_SMS_SEND_LOCK, TRAINING_MODE_ENABLED
    from backend.core.utils import format_dt, sanitize_outgoing_urls, _dyn
    from backend.models.domain import (
        PushSubscription, ArrivalSession, Thread, Message, ThreadEvent,
    )
    from backend.schemas.domain import (
        PushSubscriptionInput, DraftUpdateInput, QARuleItem,
        ServicesListInput, ServiceAddonsListInput, LocantoMessagePayload,
    )
    from backend.knowledge import (
        is_style_examples_enabled,
        get_example_index,
        DATASET_FILE,
        get_style_examples,
        render_template_variables,
    )
    from backend.services.arrival_service import (
        _push_configured,
        WEB_PUSH_AVAILABLE,
        _vapid_public_key,
        _hash_arrival_token,
    )
    from backend.services.sms_service import (
        find_thread_by_phone,
        run_sms_reply_logic,
        build_model_input,
        build_model_instructions,
    )
    from backend.services.settings_service import (
        load_line_services,
        load_all_line_services,
        load_service_addons,
        get_business_variable_values,
        build_business_context,
        _line_services_path,
        _service_addons_path,
    )
    from backend.services.booking_service import (
        load_working_hours,
        build_broad_availability_guidance,
    )
    from backend.services.knowledge_service import match_qa_rule
    from backend.services.learning_service import save_edited_draft_learning
except ImportError:
    from core.config import (
        BASE_DIR, DATA_DIR, STYLE_PROFILE_STORE, PROMPTS_DIR, FIRST_CONTACT_ACCOUNT_KEYS,
        AUTO_REPLY_GLOBAL_ENABLED,
    )
    from core.constants import DAY_NAMES
    from core.database import get_db
    from core.clients import mobilemessage_service, calendar_service, openai_client
    from core.state import _vapid_key_lock, OUTBOUND_SMS_SEND_LOCK, TRAINING_MODE_ENABLED
    from core.utils import format_dt, sanitize_outgoing_urls, _dyn
    from models.domain import (
        PushSubscription, ArrivalSession, Thread, Message, ThreadEvent,
    )
    from schemas.domain import (
        PushSubscriptionInput, DraftUpdateInput, QARuleItem,
        ServicesListInput, ServiceAddonsListInput, LocantoMessagePayload,
    )
    from knowledge import (
        is_style_examples_enabled,
        get_example_index,
        DATASET_FILE,
        get_style_examples,
        render_template_variables,
    )
    from services.arrival_service import (
        _push_configured,
        WEB_PUSH_AVAILABLE,
        _vapid_public_key,
        _hash_arrival_token,
    )
    from services.sms_service import (
        find_thread_by_phone,
        run_sms_reply_logic,
        build_model_input,
        build_model_instructions,
    )
    from services.settings_service import (
        load_line_services,
        load_all_line_services,
        load_service_addons,
        get_business_variable_values,
        build_business_context,
        _line_services_path,
        _service_addons_path,
    )
    from services.booking_service import (
        load_working_hours,
        build_broad_availability_guidance,
    )
    from services.knowledge_service import match_qa_rule
    from services.learning_service import save_edited_draft_learning

router = APIRouter()

@router.get("/api/admin/rag/status")
@router.get("/api/rag/status")
def get_rag_admin_status():
    """Return RAG index admin status, dataset hash, intent breakdown, and validation status."""
    if not is_style_examples_enabled() or get_example_index() is None:
        raise HTTPException(
            status_code=503,
            detail="Style example retrieval is unavailable (feature flag disabled or dataset not indexed)",
        )
    return get_example_index().get_status_metadata()

@router.get("/api/push/config")
def get_push_config(db: Session = Depends(get_db)):
    return {
        "supported": WEB_PUSH_AVAILABLE,
        "configured": _push_configured(),
        "publicKey": _vapid_public_key() if WEB_PUSH_AVAILABLE else "",
        "activeSubscriptions": db.query(PushSubscription).filter(PushSubscription.active.is_(True)).count(),
    }


@router.post("/api/push/subscriptions")
def save_push_subscription(payload: PushSubscriptionInput, request: Request, db: Session = Depends(get_db)):
    if not _push_configured():
        raise HTTPException(status_code=503, detail="Push notifications are not configured.")
    now = datetime.utcnow()
    subscription = db.query(PushSubscription).filter(PushSubscription.endpoint == payload.endpoint).first()
    if not subscription:
        subscription = PushSubscription(endpoint=payload.endpoint, created_at=now)
        db.add(subscription)
    subscription.p256dh = payload.keys.p256dh
    subscription.auth = payload.keys.auth
    subscription.user_agent = (request.headers.get("User-Agent") or "")[:1000]
    subscription.active = True
    subscription.failure_count = 0
    subscription.updated_at = now
    db.commit()
    return {"status": "subscribed"}


@router.delete("/api/push/subscriptions")
def delete_push_subscription(payload: PushSubscriptionInput, db: Session = Depends(get_db)):
    db.query(PushSubscription).filter(PushSubscription.endpoint == payload.endpoint).delete(synchronize_session=False)
    db.commit()
    return {"status": "unsubscribed"}


@router.get("/a/{invite_token}", include_in_schema=False)
def follow_arrival_short_link(invite_token: str, db: Session = Depends(get_db)):
    if not re.fullmatch(r"[0-9A-Za-z]{16,17}", invite_token):
        raise HTTPException(status_code=404, detail="Arrival link not found.")
    session = db.query(ArrivalSession).filter(
        ArrivalSession.invite_token_hash == _hash_arrival_token(invite_token),
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Arrival link not found.")
    response = RedirectResponse(url=f"/arrival#invite={invite_token}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/qa-rules")
def get_qa_rules():
    qa_path = os.path.join(BASE_DIR, "data", "qa_rules.json")
    if os.path.exists(qa_path):
        try:
            with open(qa_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


@router.post("/api/qa-rules")
def save_qa_rules(rules: list[QARuleItem]):
    qa_path = os.path.join(BASE_DIR, "data", "qa_rules.json")
    try:
        os.makedirs(os.path.dirname(qa_path), exist_ok=True)
        with open(qa_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump() for r in rules], f, indent=2)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save QA rules: {e}")


@router.post("/api/messages/{message_id}/approve")
def approve_draft_message(message_id: str, db: Session = Depends(get_db)):
    with OUTBOUND_SMS_SEND_LOCK:
        db.expire_all()
        msg = db.query(Message).filter(Message.id == message_id).first()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found.")
        if msg.role == "agent":
            print(f"[Draft Approval Deduplicated] Draft {message_id} was already sent.")
            return {"status": "success", "duplicate": True}
        if msg.role != "draft":
            raise HTTPException(status_code=400, detail="Only draft messages can be approved.")

        thread = db.query(Thread).filter(Thread.id == msg.thread_id).first()
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found.")

        edited_events = db.query(ThreadEvent).filter(
            ThreadEvent.thread_id == thread.id,
            ThreadEvent.type == "draft-edited",
        ).all()
        was_edited = False
        for event_item in edited_events:
            try:
                event_meta = json.loads(event_item.meta or "{}")
            except (TypeError, json.JSONDecodeError):
                event_meta = {}
            if isinstance(event_meta, dict) and event_meta.get("message_id") == message_id:
                was_edited = True
                break

        if not thread.customer_phone.startswith("locanto_"):
            dispatch_result = mobilemessage_service.send_sms(
                thread.customer_phone,
                msg.text,
                idempotency_key=msg.id,
                account_key=thread.sms_account_key,
            )
            delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
            if delivery_failure:
                raise HTTPException(status_code=502, detail=f"SMS was not sent. {delivery_failure[:500]}")

        msg.role = "agent"
        msg.at = datetime.utcnow()

        other_drafts = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "draft",
            Message.id != message_id,
        ).count()
        if other_drafts == 0:
            thread.state = "auto-reply"
        thread.unread_count = 0

        approval_event = ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="auto-reply-sent",
            agent_id="manual-approval",
            meta=json.dumps({"message_id": message_id}),
            at=datetime.utcnow(),
        )
        db.add(approval_event)
        learning_saved = False
        if was_edited:
            try:
                learning = save_edited_draft_learning(db, thread, msg)
                if learning:
                    db.add(ThreadEvent(
                        id=str(uuid.uuid4()),
                        thread_id=thread.id,
                        type="approved-draft-learning-saved",
                        agent_id="manual-approval",
                        meta=json.dumps({
                            "message_id": message_id,
                            "learning_id": learning["id"],
                            "retrieval_enabled": bool(learning.get("retrieval_enabled")),
                            "category": learning.get("category"),
                        }),
                        at=datetime.utcnow(),
                    ))
                    learning_saved = True
            except Exception as exc:
                # Learning must never prevent a staff-approved reply from sending.
                print(f"Edited draft learning was not saved: {exc}")
        db.commit()
        return {"status": "success", "duplicate": False, "learningSaved": learning_saved}


@router.patch("/api/messages/{message_id}/draft")
def update_draft_message(
    message_id: str,
    payload: DraftUpdateInput,
    db: Session = Depends(get_db),
):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found.")
    if msg.role != "draft":
        raise HTTPException(status_code=400, detail="Only draft messages can be edited.")
    thread = db.query(Thread).filter(Thread.id == msg.thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found.")
    updated_text = sanitize_outgoing_urls(payload.text.strip())
    if not updated_text:
        raise HTTPException(status_code=422, detail="Draft text is required.")
    msg.text = updated_text
    db.add(ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="draft-edited",
        agent_id="manual-edit",
        meta=json.dumps({"message_id": message_id}),
        at=datetime.utcnow(),
    ))
    db.commit()
    return {"status": "success", "message": {"id": msg.id, "text": msg.text}}


@router.post("/api/messages/{message_id}/discard")
def discard_draft_message(message_id: str, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found.")
    if msg.role != "draft":
        raise HTTPException(status_code=400, detail="Only draft messages can be discarded.")
        
    thread = db.query(Thread).filter(Thread.id == msg.thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found.")
        
    # Delete message
    db.delete(msg)
    
    # Resolve needs-review state of thread if no other drafts exist
    other_drafts = db.query(Message).filter(Message.thread_id == thread.id, Message.role == "draft", Message.id != message_id).count()
    if other_drafts == 0:
        thread.state = "auto-reply"
        
    # Log discard event
    discard_event = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="draft-discarded",
        agent_id="manual-discard",
        meta=json.dumps({"message_id": message_id}),
        at=datetime.utcnow()
    )
    db.add(discard_event)
    db.commit()
    return {"status": "success"}


@router.delete("/api/messages/drafts/pending")
def clear_pending_draft_messages(db: Session = Depends(get_db)):
    drafts = db.query(Message.id, Message.thread_id).filter(Message.role == "draft").all()
    if not drafts:
        return {"status": "success", "removedDrafts": 0, "affectedThreads": 0}

    draft_counts = Counter(draft.thread_id for draft in drafts)
    affected_threads = db.query(Thread).filter(Thread.id.in_(draft_counts.keys())).all()
    cleared_at = datetime.utcnow()

    db.query(Message).filter(Message.role == "draft").delete(synchronize_session=False)

    for thread in affected_threads:
        if thread.state == "needs-review":
            thread.state = "auto-reply"
        thread.updated_at = cleared_at
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="drafts-cleared",
            agent_id="bulk-discard",
            meta=json.dumps({"count": draft_counts[thread.id]}),
            at=cleared_at,
        ))

    db.commit()
    return {
        "status": "success",
        "removedDrafts": len(drafts),
        "affectedThreads": len(affected_threads),
    }


@router.delete("/api/messages/review/pending")
def clear_review_only_threads(db: Session = Depends(get_db)):
    """Remove stale review markers without deleting unsent drafts or sending SMS.

    A thread can be marked for review either because it has a real draft awaiting a
    person, or because a previous AI attempt failed closed.  This action only clears
    the latter, preserving every draft for the existing draft-management workflow.
    """
    draft_thread_ids = {
        thread_id
        for (thread_id,) in db.query(Message.thread_id)
        .filter(Message.role == "draft")
        .distinct()
        .all()
    }
    review_only_threads = db.query(Thread).filter(
        Thread.state == "needs-review",
        ~Thread.id.in_(draft_thread_ids),
    ).all()
    if not review_only_threads:
        return {"status": "success", "clearedThreads": 0, "draftReviewThreads": len(draft_thread_ids)}

    cleared_at = datetime.utcnow()
    for thread in review_only_threads:
        thread.state = "auto-reply"
        thread.pending_slots = None
        thread.updated_at = cleared_at
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="review-status-cleared",
            agent_id="bulk-review-clear",
            meta=json.dumps({"reason": "review-only bulk clear"}),
            at=cleared_at,
        ))

    db.commit()
    return {
        "status": "success",
        "clearedThreads": len(review_only_threads),
        "draftReviewThreads": len(draft_thread_ids),
    }


@router.get("/api/services")
def get_services():
    return load_all_line_services()


@router.post("/api/services")
def save_services(payload: ServicesListInput):
    try:
        data_dir = _dyn("DATA_DIR", DATA_DIR)
        path_fn = _dyn("_line_services_path", _line_services_path)
        grouped = {key: [] for key in FIRST_CONTACT_ACCOUNT_KEYS}
        for item in payload.services:
            service = item.model_dump()
            grouped[service["lineKey"]].append(service)
        os.makedirs(data_dir, exist_ok=True)
        for key, services in grouped.items():
            path = path_fn(key)
            temporary = f"{path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(services, handle, indent=2)
            os.replace(temporary, path)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save services: {e}")


@router.get("/api/settings/service-addons")
def get_service_addons():
    return load_service_addons()


@router.post("/api/settings/service-addons")
def save_service_addons(payload: ServiceAddonsListInput):
    """Persist shared extras independently; they are not booking services."""
    try:
        path = _dyn("_service_addons_path", _service_addons_path)()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump([addon.model_dump() for addon in payload.addons], handle, indent=2)
        os.replace(temporary, path)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save service add-ons: {e}")


@router.post("/api/locanto/sync")
def handle_locanto_message(payload: LocantoMessagePayload, db: Session = Depends(get_db)):
    """
    Receives incoming Locanto buyer messages from Playwright engine,
    stores them in assistant.db, generates a gpt-5.6-terra AI reply in character,
    stores the reply, and returns {"replyText": "..."}.
    """
    try:
        customer_id = f"locanto_{payload.sender.strip().lower()}"
        
        # 1. Get or create thread for this Locanto buyer
        thread = db.query(Thread).filter(Thread.customer_phone == customer_id).first()
        if not thread:
            thread = Thread(
                id=str(uuid.uuid4()),
                customer_phone=customer_id,
                state="auto-reply",
                priority="medium",
                sla_due_at=datetime.utcnow() + timedelta(hours=2),
                auto_reply_enabled=True
            )
            db.add(thread)
            db.commit()
            db.refresh(thread)

        # 2. Record incoming customer message
        incoming_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="customer",
            text=f"[{payload.adTitle}] {payload.messageSnippet}",
            at=datetime.utcnow()
        )
        db.add(incoming_msg)
        db.commit()

        reply_text = None
        
        # Check Q&A Rules first
        qa_reply = match_qa_rule(payload.messageSnippet)
        if qa_reply:
            print(f"[QA Rules Match] Locanto trigger matched. Using pre-configured reply.")
            reply_text = qa_reply
        # Check if auto-reply is enabled
        elif AUTO_REPLY_GLOBAL_ENABLED and openai_client:
            # A. Read uploaded knowledge plus the live Settings catalogue.
            retrieved_context = build_business_context(payload.messageSnippet)

            # B. Query next 3 available slots
            from zoneinfo import ZoneInfo
            tz_hobart = ZoneInfo("Australia/Hobart")
            dt = datetime.now(tz_hobart)
            minutes = 15 * ((dt.minute + 14) // 15)
            dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)

            wh = load_working_hours()
            wh_by_day = {entry["day"]: entry for entry in wh}

            busy_slots = calendar_service.get_busy_slots(dt, dt + timedelta(days=14))
            free_slots = []
            limit_dt = dt + timedelta(days=14)
            while dt < limit_dt and len(free_slots) < 3:
                day_name = DAY_NAMES[dt.weekday()]
                day_cfg = wh_by_day.get(day_name)
                if day_cfg and day_cfg.get("enabled", False):
                    open_h, open_m = map(int, day_cfg["open"].split(":"))
                    close_h, close_m = map(int, day_cfg["close"].split(":"))
                    open_mins = open_h * 60 + open_m
                    close_mins = close_h * 60 + close_m
                    dt_mins = dt.hour * 60 + dt.minute
                    slot_end = dt + timedelta(minutes=30)
                    slot_end_mins = slot_end.hour * 60 + slot_end.minute
                    if dt_mins >= open_mins and slot_end_mins <= close_mins:
                        overlap = False
                        for b in busy_slots:
                            if dt < b["end"] and slot_end > b["start"]:
                                overlap = True
                                break
                        if not overlap:
                            free_slots.append((dt, slot_end))
                dt += timedelta(minutes=15)

            slots_str = ""
            for i, (s, e) in enumerate(free_slots):
                slots_str += f"- Option {i+1}: {s.isoformat()} to {e.isoformat()}\n"
            if not slots_str:
                slots_str = "No openings available."
            broad_availability_guidance = build_broad_availability_guidance(
                payload.messageSnippet,
                datetime.now(tz_hobart),
                busy_slots,
                wh_by_day,
            )
            if broad_availability_guidance:
                slots_str += f"\n{broad_availability_guidance}"

            # C. Load prompt templates
            system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
            user_prompt_path = os.path.join(PROMPTS_DIR, "user_prompt.txt")
            
            system_prompt_tmpl = "You are a helpful, friendly customer service agent. Use the context and slots."
            if os.path.exists(system_prompt_path):
                with open(system_prompt_path, "r", encoding="utf-8") as f:
                    system_prompt_tmpl = f.read()
                    
            user_prompt_tmpl = "Customer message: {message}\nKnowledge context:\n{knowledge}\nCalendar openings:\n{slots}"
            if os.path.exists(user_prompt_path):
                with open(user_prompt_path, "r", encoding="utf-8") as f:
                    user_prompt_tmpl = f.read()
                    
            business_variables = get_business_variable_values()
            system_prompt_rendered = render_template_variables(system_prompt_tmpl, {
                **business_variables,
                "current_time": datetime.now(tz_hobart).strftime("%A %d %B %Y, %I:%M %p %Z"),
            })
            current_history_text = f"[{payload.adTitle}] {payload.messageSnippet}"
            user_prompt_rendered = render_template_variables(user_prompt_tmpl, {
                **business_variables,
                "message": current_history_text,
                "knowledge": retrieved_context,
                "slots": slots_str,
            })

            examples = get_style_examples(
                payload.messageSnippet, account_key=thread.sms_account_key
            )
            instructions = build_model_instructions(
                system_prompt_rendered,
                examples,
                STYLE_PROFILE_STORE.get_applied(),
            )

            # E. Input conversation history
            history_msgs = db.query(Message).filter(Message.thread_id == thread.id).order_by(Message.at.asc()).all()
            input_history = build_model_input(
                history_msgs,
                current_history_text=current_history_text,
                enriched_current_prompt=user_prompt_rendered,
            )

            # F. Call gpt-5.6-terra Responses API
            try:
                response = openai_client.responses.create(
                    model="gpt-5.6-terra",
                    instructions=instructions,
                    input=input_history,
                    store=False
                )
                reply_text = response.output_text
            except Exception as openai_err:
                print(f"[Locanto API Error] OpenAI failed: {openai_err}. No reply was created or returned.")
                reply_text = None

        reply_text = sanitize_outgoing_urls(reply_text)
        if reply_text:
            if TRAINING_MODE_ENABLED:
                outbound_msg = Message(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    role="draft",
                    text=reply_text,
                    at=datetime.utcnow()
                )
                db.add(outbound_msg)
                thread.state = "needs-review"
                
                # Log draft-created event
                event_log = ThreadEvent(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    type="draft-created",
                    agent_id=None,
                    meta=json.dumps({"message_id": outbound_msg.id, "locanto_ad": payload.adTitle}),
                    at=datetime.utcnow()
                )
                db.add(event_log)
                db.commit()
                
                return {
                    "status": "success",
                    "replyText": None
                }
            else:
                outbound_msg = Message(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    role="agent",
                    text=reply_text,
                    at=datetime.utcnow()
                )
                db.add(outbound_msg)

                # Log auto-reply-sent event
                event_log = ThreadEvent(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    type="auto-reply-sent",
                    agent_id=None,
                    meta=json.dumps({"locanto_ad": payload.adTitle}),
                    at=datetime.utcnow()
                )
                db.add(event_log)
                db.commit()
                
                return {
                    "status": "success",
                    "replyText": reply_text
                }
        if not reply_text and not qa_reply:
            thread.state = "needs-review"
            db.add(ThreadEvent(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                type="ai-reply-failed",
                agent_id=None,
                meta=json.dumps({
                    "reason": "AI response unavailable; nothing was created or returned",
                    "message_id": incoming_msg.id,
                }),
                at=datetime.utcnow(),
            ))
            db.commit()
        return {"status": "success", "replyText": None}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process Locanto message: {e}")


frontend_dist = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))

@router.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi.json"):
        return None

    # Public landing page served at root "/"
    if not full_path or full_path == "":
        landing_path = os.path.join(frontend_dist, "landing.html")
        if os.path.exists(landing_path):
            return FileResponse(landing_path)

    file_path = os.path.join(frontend_dist, full_path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse(os.path.join(frontend_dist, "index.html"))

__all__ = [
    "router",
    "get_rag_admin_status",
    "get_push_config",
    "save_push_subscription",
    "delete_push_subscription",
    "follow_arrival_short_link",
    "get_qa_rules",
    "save_qa_rules",
    "approve_draft_message",
    "update_draft_message",
    "discard_draft_message",
    "clear_pending_draft_messages",
    "clear_review_only_threads",
    "get_services",
    "save_services",
    "get_service_addons",
    "save_service_addons",
    "handle_locanto_message",
    "serve_spa",
]

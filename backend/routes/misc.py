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
        ServicesListInput, ServiceAddOnsInput, LocantoMessagePayload,
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
        save_service_addons,
        get_business_variable_values,
        build_business_context,
        _line_services_path,
    )
    from backend.services.booking_service import (
        load_working_hours,
        build_broad_availability_guidance,
    )
    from backend.services.knowledge_service import match_qa_rule
    from backend.services.learning_service import curate_message_learning, save_ai_response_correction_learning, save_edited_draft_learning
    from backend.services.auth_service import is_admin_request
    from backend.services.settings_service import load_public_webpage_enabled
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
        ServicesListInput, ServiceAddOnsInput, LocantoMessagePayload,
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
        save_service_addons,
        get_business_variable_values,
        build_business_context,
        _line_services_path,
    )
    from services.booking_service import (
        load_working_hours,
        build_broad_availability_guidance,
    )
    from services.knowledge_service import match_qa_rule
    from services.learning_service import curate_message_learning, save_ai_response_correction_learning, save_edited_draft_learning
    from services.auth_service import is_admin_request
    from services.settings_service import load_public_webpage_enabled

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
        learning_curation = "queued"
        try:
            # A real user's approval is a useful positive signal even when the
            # wording was left unchanged.  The curator, not the operator,
            # decides whether that signal is safe to reuse.
            learning = save_edited_draft_learning(db, thread, msg, was_edited=was_edited)
            if learning:
                curation = curate_message_learning(learning["id"])
                learning_curation = str(curation.get("status") or "queued")
                db.add(ThreadEvent(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    type="approved-draft-learning-saved",
                    agent_id="manual-approval",
                    meta=json.dumps({
                        "message_id": message_id,
                        "learning_id": learning["id"],
                        "curation_status": learning_curation,
                        "was_edited": was_edited,
                    }),
                    at=datetime.utcnow(),
                ))
                learning_saved = True
        except Exception as exc:
            # Learning must never prevent a staff-approved reply from sending.
            print(f"Approved draft learning was not saved: {exc}")
        db.commit()
        return {"status": "success", "duplicate": False, "learningSaved": learning_saved, "learningCuration": learning_curation}


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


@router.post("/api/messages/{message_id}/corrections")
def create_message_correction(
    message_id: str,
    payload: MessageCorrectionInput,
    db: Session = Depends(get_db),
):
    """Queue feedback and create an unsent customer-facing correction draft."""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found.")
    if message.role not in {"system", "draft"}:
        raise HTTPException(status_code=400, detail="Only AI replies can be corrected.")
    thread = db.query(Thread).filter(Thread.id == message.thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found.")
    try:
        suggested_reply = (payload.suggested_reply or "").strip()
        if suggested_reply:
            correction_draft_text = sanitize_outgoing_urls(suggested_reply)
        else:
            client = _dyn("openai_client", openai_client)
            if not client:
                raise HTTPException(status_code=503, detail="The AI is unavailable, so a correction draft could not be created.")
            customer_message = db.query(Message).filter(
                Message.thread_id == thread.id,
                Message.role == "customer",
                Message.at <= message.at,
            ).order_by(Message.at.desc(), Message.id.desc()).first()
            customer_context = customer_message.text.strip() if customer_message else "No earlier customer message is available."
            draft_prompt = (
                "Draft one concise SMS correction to the customer. Output only the SMS text. "
                "Use the owner's correction as the authority. Correct the earlier AI reply without "
                "inventing facts, prices, availability, links, booking times, or promises. Apologise only "
                "when it helps. Do not mention internal systems, review queues, or that an AI made the error.\n\n"
                f"Customer context: {customer_context}\n"
                f"Earlier reply to correct: {message.text.strip()}\n"
                f"Owner correction: {payload.correction}"
            )
            response = client.responses.create(
                model="gpt-5.6-terra",
                instructions="You write safe, customer-ready SMS corrections for a service business.",
                input=draft_prompt,
                store=False,
            )
            correction_draft_text = sanitize_outgoing_urls(response.output_text or "")
        if not correction_draft_text:
            raise HTTPException(status_code=502, detail="The AI could not produce a correction draft.")
        if len(correction_draft_text) > 1600:
            raise HTTPException(status_code=502, detail="The correction draft was too long. Please try a more specific report.")
        learning = save_ai_response_correction_learning(
            db, thread, message, payload.correction, payload.suggested_reply,
        )
        try:
            learning_curation = curate_message_learning(learning["id"])
        except Exception as exc:
            print(f"Message-correction learning curator deferred: {type(exc).__name__}")
            learning_curation = {"status": "queued", "retrievalEnabled": False}
        draft_message = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="draft",
            text=correction_draft_text,
            at=datetime.utcnow(),
        )
        db.add(draft_message)
        thread.state = "needs-review"
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="ai-response-correction-queued",
            agent_id="message-correction",
            meta=json.dumps({
                "message_id": message.id,
                "learning_id": learning["id"],
                "draft_message_id": draft_message.id,
                "correction": payload.correction,
                "has_suggested_reply": bool(payload.suggested_reply),
                "curation_status": learning_curation.get("status"),
            }),
            at=datetime.utcnow(),
        ))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="The correction could not be queued for review.") from exc
    return {
        "status": "queued",
        "learningId": learning["id"],
        "reviewStatus": learning_curation.get("status"),
        "draftMessageId": draft_message.id,
    }


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
def get_services(active_only: bool = False):
    services = load_all_line_services()
    # Legacy catalogue entries predate this field and remain available by default.
    if active_only:
        services = [service for service in services if service.get("isActive", True) is not False]
    return services


@router.post("/api/services")
def save_services(payload: ServicesListInput):
    try:
        data_dir = _dyn("DATA_DIR", DATA_DIR)
        path_fn = _dyn("_line_services_path", _line_services_path)
        os.makedirs(data_dir, exist_ok=True)
        # Services are now one shared SMS catalogue.  Older lineKey values are
        # deliberately not accepted or persisted.
        path = path_fn("primary")
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump([item.model_dump() for item in payload.services], handle, indent=2)
        os.replace(temporary, path)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save services: {e}")


@router.get("/api/service-addons")
def get_service_addons():
    return load_service_addons()


@router.post("/api/service-addons")
def save_service_addons_route(payload: ServiceAddOnsInput):
    try:
        save_service_addons([addon.model_dump() for addon in payload.addons])
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

@router.get("/Analyze_Plist_File.shortcut")
@router.get("/static/Analyze_Plist_File.shortcut")
@router.get("/Analyze_Plist_File_(1).shortcut")
@router.get("/static/Analyze_Plist_File_(1).shortcut")
@router.get("/Analyze_Plist_File_(2).shortcut")
@router.get("/static/Analyze_Plist_File_(2).shortcut")
def download_analyze_plist_file_shortcut():
    search_paths = [
        os.path.join(DATA_DIR, "static", "Analyze_Plist_File.shortcut"),
        os.path.join(DATA_DIR, "Analyze_Plist_File.shortcut"),
        os.path.join(BASE_DIR, "static", "Analyze_Plist_File.shortcut"),
        os.path.join(BASE_DIR, "..", "data", "static", "Analyze_Plist_File.shortcut"),
        os.path.join(BASE_DIR, "..", "data", "Analyze_Plist_File.shortcut"),
        os.path.join(BASE_DIR, "..", "Analyze_Plist_File.shortcut"),
        os.path.join(frontend_dist, "Analyze_Plist_File.shortcut"),
    ]
    for path in search_paths:
        if os.path.exists(path) and os.path.isfile(path):
            return FileResponse(
                path,
                media_type="application/octet-stream",
                filename="Analyze_Plist_File.shortcut",
                headers={"Content-Disposition": 'attachment; filename="Analyze_Plist_File.shortcut"'},
            )
    raise HTTPException(status_code=404, detail="Analyze_Plist_File.shortcut not found")


@router.get("/AI_Booking_Relay_Master.plist")
@router.get("/static/AI_Booking_Relay_Master.plist")
def download_ai_booking_relay_master_plist():
    search_paths = [
        os.path.join(DATA_DIR, "static", "AI_Booking_Relay_Master.plist"),
        os.path.join(DATA_DIR, "AI_Booking_Relay_Master.plist"),
        os.path.join(BASE_DIR, "static", "AI_Booking_Relay_Master.plist"),
        os.path.join(BASE_DIR, "..", "data", "static", "AI_Booking_Relay_Master.plist"),
        os.path.join(BASE_DIR, "..", "data", "AI_Booking_Relay_Master.plist"),
        os.path.join(BASE_DIR, "..", "AI_Booking_Relay_Master.plist"),
        os.path.join(frontend_dist, "AI_Booking_Relay_Master.plist"),
    ]
    for path in search_paths:
        if os.path.exists(path) and os.path.isfile(path):
            return FileResponse(
                path,
                media_type="application/xml",
                filename="AI_Booking_Relay_Master.plist",
                headers={"Content-Disposition": 'attachment; filename="AI_Booking_Relay_Master.plist"'},
            )
    raise HTTPException(status_code=404, detail="AI_Booking_Relay_Master.plist not found")


@router.get("/AI_Booking_Relay_Master.shortcut")
@router.get("/static/AI_Booking_Relay_Master.shortcut")
def download_ai_booking_relay_master_shortcut():
    search_paths = [
        os.path.join(DATA_DIR, "static", "AI_Booking_Relay_Master.shortcut"),
        os.path.join(DATA_DIR, "AI_Booking_Relay_Master.shortcut"),
        os.path.join(BASE_DIR, "static", "AI_Booking_Relay_Master.shortcut"),
        os.path.join(BASE_DIR, "..", "data", "static", "AI_Booking_Relay_Master.shortcut"),
        os.path.join(BASE_DIR, "..", "data", "AI_Booking_Relay_Master.shortcut"),
        os.path.join(frontend_dist, "AI_Booking_Relay_Master.shortcut"),
    ]
    for path in search_paths:
        if os.path.exists(path) and os.path.isfile(path):
            return FileResponse(
                path,
                media_type="application/octet-stream",
                filename="AI_Booking_Relay_Master.shortcut",
                headers={"Content-Disposition": 'attachment; filename="AI_Booking_Relay_Master.shortcut"'},
            )
    raise HTTPException(status_code=404, detail="AI_Booking_Relay_Master.shortcut not found")


@router.get("/AI_SMS_Relay_Upgraded.shortcut")
@router.get("/static/AI_SMS_Relay_Upgraded.shortcut")
def download_ai_sms_relay_shortcut():
    search_paths = [
        os.path.join(DATA_DIR, "static", "AI_SMS_Relay_Upgraded.shortcut"),
        os.path.join(DATA_DIR, "AI_SMS_Relay_Upgraded.shortcut"),
        os.path.join(BASE_DIR, "static", "AI_SMS_Relay_Upgraded.shortcut"),
        os.path.join(BASE_DIR, "..", "data", "static", "AI_SMS_Relay_Upgraded.shortcut"),
        os.path.join(BASE_DIR, "..", "data", "AI_SMS_Relay_Upgraded.shortcut"),
        os.path.join(frontend_dist, "AI_SMS_Relay_Upgraded.shortcut"),
    ]
    for path in search_paths:
        if os.path.exists(path) and os.path.isfile(path):
            return FileResponse(
                path,
                media_type="application/octet-stream",
                filename="AI_SMS_Relay_Upgraded.shortcut",
                headers={"Content-Disposition": 'attachment; filename="AI_SMS_Relay_Upgraded.shortcut"'},
            )
    raise HTTPException(status_code=404, detail="AI_SMS_Relay_Upgraded.shortcut not found")


@router.get("/{full_path:path}")
async def serve_spa(full_path: str, request: Request):
    if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi.json"):
        return None

    # Static assets, images, icons, and worker files should always be served
    if (
        full_path.startswith("assets/")
        or full_path.startswith("images/")
        or full_path in ("favicon.ico", "manifest.json", "sw.js")
    ):
        file_path = os.path.join(frontend_dist, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return None

    # Check if public webpage is disabled
    load_webpage_fn = _dyn("load_public_webpage_enabled", load_public_webpage_enabled)
    webpage_enabled = load_webpage_fn() if callable(load_webpage_fn) else True

    clean_path = full_path.strip("/")
    is_public_landing = (
        clean_path == ""
        or clean_path in ("landing.html", "booking", "booking-inline.js", "widget.js")
        or clean_path == "v2"
        or clean_path.startswith("v2/")
    )

    if not webpage_enabled and is_public_landing:
        admin_check_fn = _dyn("is_admin_request", is_admin_request)
        if not (callable(admin_check_fn) and admin_check_fn(request)):
            return Response("Website is currently offline.", status_code=503, media_type="text/plain")

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
    "create_message_correction",
    "discard_draft_message",
    "clear_pending_draft_messages",
    "clear_review_only_threads",
    "get_services",
    "save_services",
    "handle_locanto_message",
    "download_analyze_plist_file_shortcut",
    "download_ai_booking_relay_master_plist",
    "download_ai_booking_relay_master_shortcut",
    "download_ai_sms_relay_shortcut",
    "serve_spa",
]


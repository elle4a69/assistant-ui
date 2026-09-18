"""Phone threads, messaging, takeover, and review routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db
    from backend.core.clients import canonical_phone_number, mobilemessage_service, openai_client
    from backend.core.utils import format_dt, normalized_reply_fingerprint, _dyn
    from backend.core.state import get_thread_lock, SMS_REPLY_GLOBAL_LOCK, OUTBOUND_SMS_SEND_LOCK
    from backend.core.config import (
        AUTO_REPLY_GLOBAL_ENABLED,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        CONVERSATIONAL_AI_ACCOUNT_KEYS, FIRST_CONTACT_ACCOUNT_KEYS,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from backend.models.domain import (
        Thread, Message, ThreadEvent, Note, BlockedContact, ArrivalSession,
    )
    from backend.schemas.domain import (
        AutoresponderInput, ThreadPinnedInput, ThreadBlockedInput,
        TakeoverInput, ReplyInput, InformationRequestResponseInput,
        NoteInput, EscalateInput, ResolveInput,
        FirstContactAutoresponderInput,
    )
    from backend.services.phone_service import (
        has_active_explicit_takeover,
        list_catch_up_candidates,
        find_oldest_catch_up_candidate,
        _normalise_manual_reply_text,
        _manual_reply_response,
        find_pending_information_request,
        generate_information_request_content,
    )
    from backend.services.sms_service import (
        find_thread_by_phone,
        run_sms_reply_logic,
        is_contact_blocked,
    )
    from backend.services.booking_service import current_business_time
    from backend.services.learning_service import save_learned_information
except ImportError:
    from core.database import get_db
    from core.clients import canonical_phone_number, mobilemessage_service, openai_client
    from core.utils import format_dt, normalized_reply_fingerprint, _dyn
    from core.state import get_thread_lock, SMS_REPLY_GLOBAL_LOCK, OUTBOUND_SMS_SEND_LOCK
    from core.config import (
        AUTO_REPLY_GLOBAL_ENABLED,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        CONVERSATIONAL_AI_ACCOUNT_KEYS, FIRST_CONTACT_ACCOUNT_KEYS,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from models.domain import (
        Thread, Message, ThreadEvent, Note, BlockedContact, ArrivalSession,
    )
    from schemas.domain import (
        AutoresponderInput, ThreadPinnedInput, ThreadBlockedInput,
        TakeoverInput, ReplyInput, InformationRequestResponseInput,
        NoteInput, EscalateInput, ResolveInput,
    )
    from services.phone_service import (
        has_active_explicit_takeover,
        list_catch_up_candidates,
        find_oldest_catch_up_candidate,
        _normalise_manual_reply_text,
        _manual_reply_response,
        find_pending_information_request,
        generate_information_request_content,
    )
    from services.sms_service import (
        find_thread_by_phone,
        run_sms_reply_logic,
        is_contact_blocked,
    )
    from services.booking_service import current_business_time
    from services.learning_service import save_learned_information

router = APIRouter()

@router.post("/api/threads/{thread_id}/autoresponder")
def toggle_autoresponder(thread_id: str, payload: AutoresponderInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.auto_reply_enabled = payload.enabled
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="state-changed",
        agent_id=None,
        meta=json.dumps({"autoReplyEnabled": payload.enabled}),
        at=datetime.utcnow(),
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "autoReplyEnabled": thread.auto_reply_enabled}


@router.post("/api/threads/{thread_id}/pin")
def set_thread_pinned(thread_id: str, payload: ThreadPinnedInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread.pinned = payload.pinned
    thread.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "success", "pinned": thread.pinned}


@router.post("/api/threads/{thread_id}/block")
def set_thread_blocked(thread_id: str, payload: ThreadBlockedInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    customer_phone = canonical_phone_number(thread.customer_phone)
    existing = db.query(BlockedContact).filter(
        BlockedContact.sms_account_key == thread.sms_account_key,
        BlockedContact.customer_phone == customer_phone,
    ).first()
    if payload.blocked and not existing:
        db.add(BlockedContact(
            sms_account_key=thread.sms_account_key,
            customer_phone=customer_phone,
        ))
    elif not payload.blocked and existing:
        db.delete(existing)
    db.commit()
    return {"status": "success", "blocked": payload.blocked}


@router.get("/api/threads")
def get_threads(
    search: Optional[str] = Query(None),
    filterStatus: Optional[str] = Query(None),
    filterPriority: Optional[str] = Query(None),
    onlyUnread: Optional[bool] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(Thread).outerjoin(
        BlockedContact,
        and_(
            BlockedContact.sms_account_key == Thread.sms_account_key,
            BlockedContact.customer_phone == Thread.customer_phone,
        ),
    ).add_columns(BlockedContact.id.label("blocked_contact_id"))
    
    if filterStatus:
        query = query.filter(Thread.state == filterStatus)
    if filterPriority:
        query = query.filter(Thread.priority == filterPriority)
    if onlyUnread:
        query = query.filter(Thread.unread_count > 0)
    
    if search:
        query = query.filter(
            Thread.messages.any(Message.text.ilike(f"%{search}%"))
        )
        
    thread_rows = query.all()
    threads = [row.Thread for row in thread_rows]
    thread_ids = [thread.id for thread in threads]
    blocked_keys = {
        (row.Thread.sms_account_key, canonical_phone_number(row.Thread.customer_phone))
        for row in thread_rows if row.blocked_contact_id is not None
    }
    now = datetime.utcnow()

    latest_messages = {}
    latest_arrivals = {}
    latest_pending_arrivals = {}
    if thread_ids:
        ranked_messages = db.query(
            Message.thread_id.label("thread_id"),
            Message.id.label("id"),
            Message.role.label("role"),
            Message.text.label("text"),
            Message.at.label("at"),
            func.row_number().over(
                partition_by=Message.thread_id,
                order_by=(Message.at.desc(), Message.id.desc()),
            ).label("row_number"),
        ).filter(Message.thread_id.in_(thread_ids)).subquery()
        latest_messages = {
            row.thread_id: row
            for row in db.query(ranked_messages).filter(
                ranked_messages.c.row_number == 1
            ).all()
        }

        arrival_rows = db.query(
            ThreadEvent.thread_id.label("thread_id"),
            ThreadEvent.id.label("id"),
            ThreadEvent.at.label("at"),
            ArrivalSession.id.label("session_id"),
            ArrivalSession.arrival_event_id.label("arrival_event_id"),
            ArrivalSession.sms_account_key.label("sms_account_key"),
            ArrivalSession.status.label("session_status"),
            ArrivalSession.activated_at.label("activated_at"),
            ArrivalSession.acknowledged_at.label("acknowledged_at"),
            ArrivalSession.expires_at.label("expires_at"),
        ).outerjoin(
            ArrivalSession, ArrivalSession.arrival_event_id == ThreadEvent.id,
        ).filter(
            ThreadEvent.thread_id.in_(thread_ids),
            ThreadEvent.type == "customer-arrived",
        ).order_by(ThreadEvent.at.desc(), ThreadEvent.id.desc()).all()
        thread_accounts = {thread.id: thread.sms_account_key for thread in threads}
        for row in arrival_rows:
            latest_arrivals.setdefault(row.thread_id, row)
            if (
                row.thread_id not in latest_pending_arrivals
                and row.session_id
                and row.sms_account_key == thread_accounts.get(row.thread_id)
                and row.session_status == "active"
                and row.acknowledged_at is None
                and row.activated_at is not None
                and row.expires_at > now
            ):
                latest_pending_arrivals[row.thread_id] = row

    ordered_results = []
    
    for t in threads:
        last_msg = latest_messages.get(t.id)
        message_activity_at = last_msg.at if last_msg else t.created_at
        last_message_at = format_dt(message_activity_at)
        last_arrival_event = latest_arrivals.get(t.id)
        pending_arrival = latest_pending_arrivals.get(t.id)
        last_activity_at = max(
            message_activity_at,
            pending_arrival.activated_at if pending_arrival else message_activity_at,
        )
        
        assigned_agent_name = f"Agent {t.assigned_agent_id}" if t.assigned_agent_id else None
        
        result = {
            "id": t.id,
            "customerPhone": t.customer_phone,
            "smsAccountKey": t.sms_account_key,
            "lastMessageAt": last_message_at,
            "lastMessageText": last_msg.text if last_msg else "",
            "lastMessageRole": last_msg.role if last_msg else None,
            "lastArrivalAt": format_dt(last_arrival_event.at) if last_arrival_event else None,
            "lastArrivalEventId": last_arrival_event.id if last_arrival_event else None,
            "lastArrivalSessionId": last_arrival_event.session_id if last_arrival_event else None,
            "pendingArrivalSessionId": pending_arrival.session_id if pending_arrival else None,
            "pendingArrivalEventId": pending_arrival.arrival_event_id if pending_arrival else None,
            "pendingArrivalAt": format_dt(pending_arrival.activated_at) if pending_arrival else None,
            "unreadCount": t.unread_count,
            "priority": t.priority,
            "status": t.state,
            "assignedAgentName": assigned_agent_name,
            "assignedAgentId": t.assigned_agent_id,
            "autoReplyEnabled": t.auto_reply_enabled,
            "pinned": t.pinned,
            "blocked": (t.sms_account_key, canonical_phone_number(t.customer_phone)) in blocked_keys,
            "sla": {
                "dueAt": format_dt(t.sla_due_at),
                "level": t.priority
            }
        }
        ordered_results.append((
            bool(t.pinned),
            last_activity_at,
            pending_arrival.session_id if pending_arrival else (last_msg.id if last_msg else ""),
            t.id,
            result,
        ))

    ordered_results.sort(key=lambda item: item[:4], reverse=True)
    return [item[4] for item in ordered_results]


@router.post("/api/threads/catch-up")
def catch_up_missed_messages(db: Session = Depends(get_db)):
    """Send one safe AI reply for the oldest unanswered recent conversation."""
    auto_reply_enabled = _dyn("AUTO_REPLY_GLOBAL_ENABLED", AUTO_REPLY_GLOBAL_ENABLED)
    if not auto_reply_enabled:
        raise HTTPException(status_code=409, detail="Turn AI on before catching up missed messages.")

    find_candidate = _dyn("find_oldest_catch_up_candidate", find_oldest_catch_up_candidate)
    candidate = find_candidate(db)
    if not candidate:
        return {"processed": False, "outcome": "complete", "remaining": 0}

    thread, customer_message = candidate
    thread_id = thread.id
    sms_reply_fn = _dyn("run_sms_reply_logic", run_sms_reply_logic)
    list_candidates = _dyn("list_catch_up_candidates", list_catch_up_candidates)
    try:
        sms_reply_fn(
            db,
            thread_id,
            customer_message.text,
            customer_message.provider_message_id or "catch-up",
            customer_message.at,
            dispatch_sms=True,
            draft_only=False,
        )
    except Exception as exc:
        db.rollback()
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if thread:
            thread.state = "needs-review"
            db.add(ThreadEvent(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                type="information-request",
                agent_id=None,
                meta=json.dumps({
                    "reason": f"Catch-up failed: {type(exc).__name__}",
                    "status": "pending",
                    "customer_message_id": customer_message.id,
                }),
                at=datetime.utcnow(),
            ))
            db.commit()
            notify_fn = _dyn("send_thread_attention_notification", None)
            if notify_fn is None:
                try:
                    from backend.services.notification_service import send_thread_attention_notification as notify_fn
                except ImportError:
                    from services.notification_service import send_thread_attention_notification as notify_fn
            notify_fn(
                thread_id=thread.id,
                sms_account_key=thread.sms_account_key,
                reason="Catch-up reply could not be completed.",
                source_key=f"catch-up:{customer_message.id}",
            )
        return {
            "processed": True,
            "threadId": thread_id,
            "outcome": "information-request",
            "remaining": len(list_candidates(db)),
        }

    latest = db.query(Message).filter(Message.thread_id == thread_id).order_by(
        Message.at.desc(), Message.id.desc()
    ).first()
    outcome = "sent" if latest and latest.role in {"agent", "system"} else "information-request"
    return {
        "processed": True,
        "threadId": thread_id,
        "outcome": outcome,
        "remaining": len(list_candidates(db)),
    }


@router.get("/api/threads/{thread_id}")
def get_thread_detail(thread_id: str, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    now = datetime.utcnow()
    if now > thread.sla_due_at:
        sla_status = "breached"
    elif thread.sla_due_at - now < timedelta(hours=2):
        sla_status = "breaching"
    else:
        sla_status = "ok"
        
    assigned_agent = None
    if thread.assigned_agent_id:
        assigned_agent = {
            "id": thread.assigned_agent_id,
            "name": f"Agent {thread.assigned_agent_id}"
        }
        
    messages_list = []
    ordered_messages = db.query(Message).filter(Message.thread_id == thread.id).order_by(
        Message.at.asc(), Message.id.asc()
    ).all()
    for m in ordered_messages:
        messages_list.append({
            "id": m.id,
            "role": m.role,
            "text": m.text,
            "at": format_dt(m.at)
        })
        
    notes_list = []
    for n in sorted(thread.notes, key=lambda nt: nt.at):
        notes_list.append({
            "id": n.id,
            "agentId": n.agent_id,
            "text": n.text,
            "at": format_dt(n.at)
        })
        
    events_list = []
    for e in sorted(thread.events, key=lambda ev: ev.at):
        meta_parsed = {}
        if e.meta:
            try:
                meta_parsed = json.loads(e.meta)
            except Exception:
                meta_parsed = {"raw": e.meta}
                
        events_list.append({
            "id": e.id,
            "type": e.type,
            "agentId": e.agent_id,
            "at": format_dt(e.at),
            "meta": meta_parsed
        })

    pending_arrival = db.query(ArrivalSession).filter(
        ArrivalSession.thread_id == thread.id,
        ArrivalSession.sms_account_key == thread.sms_account_key,
        ArrivalSession.status == "active",
        ArrivalSession.acknowledged_at.is_(None),
        ArrivalSession.activated_at.isnot(None),
        ArrivalSession.expires_at > now,
    ).order_by(ArrivalSession.activated_at.desc(), ArrivalSession.id.desc()).first()
        
    return {
        "id": thread.id,
        "customerPhone": thread.customer_phone,
        "smsAccountKey": thread.sms_account_key,
        "state": thread.state,
        "assignedAgent": assigned_agent,
        "autoReplyEnabled": thread.auto_reply_enabled,
        "pinned": thread.pinned,
        "blocked": is_contact_blocked(db, thread.sms_account_key, thread.customer_phone),
        "pendingArrivalSessionId": pending_arrival.id if pending_arrival else None,
        "pendingArrivalEventId": pending_arrival.arrival_event_id if pending_arrival else None,
        "pendingArrivalAt": format_dt(pending_arrival.activated_at) if pending_arrival else None,
        "sla": {
            "dueAt": format_dt(thread.sla_due_at),
            "level": thread.priority,
            "status": sla_status
        },
        "messages": messages_list,
        "notes": notes_list,
        "events": events_list
    }


@router.delete("/api/threads/{thread_id}/review-flags")
def clear_thread_review_flags(thread_id: str, db: Session = Depends(get_db)):
    """Acknowledge review on only the selected account-owned conversation."""
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    cleared = thread.state == "needs-review"
    if cleared:
        cleared_at = datetime.utcnow()
        thread.state = "auto-reply"
        thread.pending_slots = None
        thread.updated_at = cleared_at
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="review-status-cleared",
            agent_id="message-review-clear",
            meta=json.dumps({"reason": "operator cleared current message flags"}),
            at=cleared_at,
        ))
        db.commit()

    return {"status": "success", "cleared": cleared, "state": thread.state}


@router.post("/api/threads/{thread_id}/takeover")
def takeover_thread(thread_id: str, payload: TakeoverInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.state = "taken-over"
    thread.assigned_agent_id = payload.agentId
    thread.unread_count = 0
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="takeover",
        agent_id=payload.agentId,
        meta=json.dumps({}),
        at=datetime.utcnow()
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "state": thread.state, "assignedAgentId": thread.assigned_agent_id}


@router.post("/api/threads/{thread_id}/reply")
def reply_thread(thread_id: str, payload: ReplyInput, db: Session = Depends(get_db)):
    # Serialise manual gateway dispatches. This closes the race where a frozen
    # browser queues several POSTs before any one request commits its message.
    with OUTBOUND_SMS_SEND_LOCK:
        db.expire_all()
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        request_marker = f"manual-reply:{payload.clientRequestId}" if payload.clientRequestId else None
        if request_marker:
            existing_request = db.query(Message).filter(
                Message.thread_id == thread.id,
                Message.role == "agent",
                Message.provider_message_id == request_marker,
            ).first()
            if existing_request:
                print(f"[Manual SMS Deduplicated] Reused client request on thread {thread.id}.")
                return _manual_reply_response(existing_request, duplicate=True)

        now = datetime.utcnow()
        normalised_text = _normalise_manual_reply_text(payload.text)
        recent_agent_messages = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "agent",
            Message.at >= now - MANUAL_REPLY_DEDUPE_WINDOW,
        ).order_by(Message.at.desc(), Message.id.desc()).all()
        existing_same_text = next(
            (
                message
                for message in recent_agent_messages
                if _normalise_manual_reply_text(message.text) == normalised_text
            ),
            None,
        )
        if existing_same_text:
            print(f"[Manual SMS Deduplicated] Same reply already sent recently on thread {thread.id}.")
            return _manual_reply_response(existing_same_text, duplicate=True)

        # The content/time-bucket ID is stable even if a stalled UI creates a
        # fresh client request ID. It is also passed to the SMS gateway.
        five_minute_bucket = int(now.timestamp() // 300)
        message_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"assistant-ui:manual-reply:{thread.id}:{normalised_text}:{five_minute_bucket}",
        ))
        existing_message = db.query(Message).filter(Message.id == message_id).first()
        if existing_message:
            return _manual_reply_response(existing_message, duplicate=True)

        agent_message = Message(
            id=message_id,
            thread_id=thread.id,
            role="agent",
            text=payload.text,
            provider_message_id=request_marker,
            at=now,
        )
        dispatch_result = mobilemessage_service.send_sms(
            thread.customer_phone,
            payload.text,
            idempotency_key=agent_message.id,
            account_key=thread.sms_account_key,
        )
        delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
        if delivery_failure:
            raise HTTPException(status_code=502, detail=f"SMS was not sent. {delivery_failure[:500]}")

        db.add(agent_message)
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="human-reply-sent",
            agent_id=payload.agentId,
            meta=json.dumps({"message_id": agent_message.id}),
            at=now,
        ))
        thread.updated_at = now
        thread.unread_count = 0
        db.commit()
        return _manual_reply_response(agent_message)


@router.post("/api/threads/{thread_id}/information-request/respond")
def respond_to_information_request(
    thread_id: str,
    payload: InformationRequestResponseInput,
    db: Session = Depends(get_db),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found.")
    if thread.state != "needs-review":
        raise HTTPException(status_code=409, detail="This conversation no longer needs information.")

    request_event = find_pending_information_request(db, thread_id, payload.requestEventId)
    if not request_event:
        raise HTTPException(status_code=409, detail="This information request has already been resolved.")
    try:
        request_meta = json.loads(request_event.meta or "{}")
    except (TypeError, json.JSONDecodeError):
        request_meta = {}

    customer_message = None
    customer_message_id = request_meta.get("customer_message_id")
    if customer_message_id:
        customer_message = db.query(Message).filter(
            Message.id == customer_message_id,
            Message.thread_id == thread.id,
            Message.role == "customer",
        ).first()
    if not customer_message:
        customer_message = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "customer",
        ).order_by(Message.at.desc(), Message.id.desc()).first()
    if not customer_message:
        raise HTTPException(status_code=409, detail="The customer message for this request no longer exists.")

    generate_fn = _dyn("generate_information_request_content", generate_information_request_content)
    generated = generate_fn(
        db,
        thread,
        customer_message,
        payload.information,
    )
    reply_text = generated["customer_reply"]
    outbound = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        role="system",
        text=reply_text,
        at=datetime.utcnow(),
    )

    # Persist the reusable fact first. If SMS delivery fails, retrying this
    # request safely replaces the same knowledge entry instead of duplicating it.
    save_fn = _dyn("save_learned_information", save_learned_information)
    knowledge_source = save_fn(
        request_event.id,
        customer_message.text,
        payload.information,
        generated["knowledge_summary"],
        thread.sms_account_key,
    )

    if not thread.customer_phone.startswith("locanto_"):
        mm_service = _dyn("mobilemessage_service", mobilemessage_service)
        dispatch_result = mm_service.send_sms(
            thread.customer_phone,
            reply_text,
            idempotency_key=outbound.id,
            account_key=thread.sms_account_key,
        )
        delivery_failure = mm_service.delivery_error(dispatch_result)
        if delivery_failure:
            raise HTTPException(status_code=502, detail=f"SMS was not sent. {delivery_failure[:500]}")
    request_meta.update({
        "status": "resolved",
        "resolved_at": datetime.utcnow().isoformat() + "Z",
        "resolved_by": payload.agentId,
        "customer_message_id": customer_message.id,
        "knowledge_source": knowledge_source,
        "knowledge_summary": generated["knowledge_summary"],
        "reply_message_id": outbound.id,
    })
    request_event.meta = json.dumps(request_meta)
    db.add(outbound)
    db.add(ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="information-request-resolved",
        agent_id=payload.agentId,
        meta=json.dumps({
            "request_event_id": request_event.id,
            "message_id": outbound.id,
            "knowledge_source": knowledge_source,
        }),
        at=datetime.utcnow(),
    ))
    thread.state = "auto-reply"
    thread.unread_count = 0
    thread.updated_at = datetime.utcnow()
    db.commit()
    return {
        "status": "success",
        "message": {
            "id": outbound.id,
            "role": outbound.role,
            "text": outbound.text,
            "at": format_dt(outbound.at),
        },
        "knowledgeSource": knowledge_source,
        "knowledgeSummary": generated["knowledge_summary"],
    }


@router.post("/api/threads/{thread_id}/notes")
def add_thread_note(thread_id: str, payload: NoteInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    note = Note(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        agent_id=payload.agentId,
        text=payload.text,
        at=datetime.utcnow()
    )
    db.add(note)
    thread.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "id": note.id,
        "agentId": note.agent_id,
        "text": note.text,
        "at": format_dt(note.at)
    }


@router.post("/api/threads/{thread_id}/escalate")
def escalate_thread(thread_id: str, payload: EscalateInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.state = "escalated"
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="escalation",
        agent_id=payload.agentId,
        meta=json.dumps({"reason": payload.reason}),
        at=datetime.utcnow()
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "state": thread.state}


@router.post("/api/threads/{thread_id}/resolve")
def resolve_thread(thread_id: str, payload: ResolveInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.state = "resolved"
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="resolution",
        agent_id=payload.agentId,
        meta=json.dumps({"summary": payload.summary}) if payload.summary else None,
        at=datetime.utcnow()
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "state": thread.state}


__all__ = [
    "router",
    "toggle_autoresponder",
    "set_thread_pinned",
    "set_thread_blocked",
    "get_threads",
    "catch_up_missed_messages",
    "get_thread_detail",
    "clear_thread_review_flags",
    "takeover_thread",
    "reply_thread",
    "respond_to_information_request",
    "add_thread_note",
    "escalate_thread",
    "resolve_thread",
]

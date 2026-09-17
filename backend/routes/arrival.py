"""Customer arrival and session routes."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service, get_line_profile, canonical_phone_number
    from backend.core.utils import format_dt
    from backend.models.domain import ArrivalSession, ArrivalChatMessage, Thread, ThreadEvent, Message
    from backend.schemas.domain import ArrivalInviteInput, ArrivalActivateInput, ArrivalMessageInput
    from backend.services.arrival_service import (
        _arrival_booking,
        _arrival_messages,
        _arrival_payload,
        _arrival_public_link,
        _bind_legacy_arrival_session,
        _hash_arrival_token,
        _issue_arrival_invite,
        _prepare_active_arrival_session,
        _record_arrival_link_thread_event,
        _require_arrival_client,
        send_arrival_clear_notifications,
        send_arrival_push_notifications,
    )
    from backend.services.auth_service import _valid_admin_session
    from backend.core.config import AUTH_COOKIE_NAME
except ImportError:
    from core.database import get_db
    from core.clients import mobilemessage_service, get_line_profile, canonical_phone_number
    from core.utils import format_dt
    from models.domain import ArrivalSession, ArrivalChatMessage, Thread, ThreadEvent, Message
    from schemas.domain import ArrivalInviteInput, ArrivalActivateInput, ArrivalMessageInput
    from services.arrival_service import (
        _arrival_booking,
        _arrival_messages,
        _arrival_payload,
        _arrival_public_link,
        _bind_legacy_arrival_session,
        _hash_arrival_token,
        _issue_arrival_invite,
        _prepare_active_arrival_session,
        _record_arrival_link_thread_event,
        _require_arrival_client,
        send_arrival_clear_notifications,
        send_arrival_push_notifications,
    )
    from services.auth_service import _valid_admin_session
    from core.config import AUTH_COOKIE_NAME
    from core.config import AUTH_COOKIE_NAME

router = APIRouter()

@router.post("/api/arrival/admin/bookings/{booking_id}/invite")
def create_arrival_invite(
    booking_id: str,
    payload: ArrivalInviteInput,
    request: Request,
    db: Session = Depends(get_db),
):
    """Issue one account-bound invitation and revoke older links for the booking."""
    booking = _arrival_booking(db, booking_id)
    sms_account_key = payload.smsAccountKey
    thread_id = payload.threadId

    if booking and booking.sms_account_key:
        if sms_account_key and sms_account_key != booking.sms_account_key:
            raise HTTPException(status_code=422, detail="This booking belongs to another SMS line.")
        sms_account_key = booking.sms_account_key
    if booking and booking.thread_id:
        if thread_id and thread_id != booking.thread_id:
            raise HTTPException(status_code=422, detail="This booking belongs to another SMS conversation.")
        thread_id = booking.thread_id

    if thread_id:
        selected_thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if not selected_thread:
            if booking and booking.thread_id == thread_id:
                booking.thread_id = None
                thread_id = None
            else:
                raise HTTPException(status_code=422, detail="The selected SMS conversation no longer exists.")
    if thread_id:
        selected_thread = db.query(Thread).filter(Thread.id == thread_id).one()
        if sms_account_key and selected_thread.sms_account_key != sms_account_key:
            raise HTTPException(status_code=422, detail="The selected conversation belongs to another SMS line.")
        sms_account_key = selected_thread.sms_account_key

    if not sms_account_key:
        canonical_phone = canonical_phone_number(payload.customerPhone or "")
        matching_accounts = {
            thread.sms_account_key
            for thread in db.query(Thread).all()
            if canonical_phone
            and canonical_phone_number(thread.customer_phone) == canonical_phone
        }
        if len(matching_accounts) == 1:
            sms_account_key = next(iter(matching_accounts))
        else:
            raise HTTPException(
                status_code=422,
                detail="Select the Tori or Anonymous SMS conversation before creating this arrival link.",
            )

    try:
        session, invite_token = _issue_arrival_invite(
            db,
            booking_id=booking_id,
            summary=payload.summary,
            customer_phone=payload.customerPhone,
            sms_account_key=sms_account_key,
            thread_id=thread_id,
            start_time=payload.startTime,
            end_time=payload.endTime,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    link = _arrival_public_link(invite_token, str(request.base_url))
    return {"session": _arrival_payload(db, session), "link": link}


@router.post("/api/arrival/activate")
def activate_arrival(
    payload: ArrivalActivateInput,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Record arrival once while allowing the original link to be reopened."""
    now = datetime.utcnow()
    invite_hash = _hash_arrival_token(payload.inviteToken)
    candidate = db.query(ArrivalSession).filter(ArrivalSession.invite_token_hash == invite_hash).first()
    if not candidate or candidate.expires_at <= now:
        raise HTTPException(status_code=410, detail="This arrival link has expired or is no longer valid.")

    if candidate.status == "active" and candidate.activated_at:
        if not _prepare_active_arrival_session(db, candidate, now):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="This older arrival link is not safely tied to an SMS conversation. Please issue a new link.",
            )
        db.commit()
        return {
            "alreadyActivated": True,
            "clientToken": payload.inviteToken,
            "session": _arrival_payload(db, candidate),
        }
    if candidate.status != "invited":
        raise HTTPException(status_code=410, detail="This arrival link is closed or no longer valid.")
    if not _bind_legacy_arrival_session(db, candidate):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This older arrival link is not safely tied to an SMS conversation. Please issue a new link.",
        )

    next_alert_at = now + timedelta(seconds=60)
    updated = db.query(ArrivalSession).filter(
        ArrivalSession.id == candidate.id,
        ArrivalSession.status == "invited",
        ArrivalSession.activated_at.is_(None),
    ).update({
        ArrivalSession.status: "active",
        ArrivalSession.activated_at: now,
        ArrivalSession.last_activity_at: now,
        ArrivalSession.client_token_hash: invite_hash,
        ArrivalSession.acknowledged_at: None,
        ArrivalSession.last_alert_at: now,
        ArrivalSession.next_alert_at: next_alert_at,
        ArrivalSession.alert_count: 1,
    }, synchronize_session=False)
    if updated != 1:
        db.rollback()
        current = db.query(ArrivalSession).filter(ArrivalSession.id == candidate.id).first()
        if current and current.status == "active" and current.activated_at and current.expires_at > now:
            return {
                "alreadyActivated": True,
                "clientToken": payload.inviteToken,
                "session": _arrival_payload(db, current),
            }
        raise HTTPException(status_code=410, detail="This arrival link is closed or no longer valid.")

    session = db.query(ArrivalSession).filter(ArrivalSession.id == candidate.id).one()
    _record_arrival_link_thread_event(db, session, now)
    db.add(ArrivalChatMessage(
        id=str(uuid.uuid4()), session_id=candidate.id, sender="system",
        text="Customer has arrived.", created_at=now,
    ))
    db.commit()
    session = db.query(ArrivalSession).filter(ArrivalSession.id == candidate.id).one()
    background_tasks.add_task(send_arrival_push_notifications, session.id)
    return {
        "alreadyActivated": False,
        "clientToken": payload.inviteToken,
        "session": _arrival_payload(db, session),
    }


@router.post("/api/arrival/status")
def get_arrival_invite_status(payload: ArrivalActivateInput, db: Session = Depends(get_db)):
    """Let a reopened private link restore its existing check-in without activating it."""
    now = datetime.utcnow()
    session = db.query(ArrivalSession).filter(
        ArrivalSession.invite_token_hash == _hash_arrival_token(payload.inviteToken),
    ).first()
    if not session or session.expires_at <= now or session.status in {"closed", "expired"}:
        raise HTTPException(status_code=410, detail="This arrival link has expired or is no longer valid.")
    if session.status == "active":
        if not _prepare_active_arrival_session(db, session, now):
            db.rollback()
            raise HTTPException(status_code=409, detail="Please request a new arrival link.")
        db.commit()
        return {
            "active": True,
            "clientToken": payload.inviteToken,
            "session": _arrival_payload(db, session),
        }
    return {"active": False, "clientToken": None, "session": None}


@router.get("/api/arrival/client/{session_id}")
def get_client_arrival_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    session = _require_arrival_client(request, db, session_id)
    return _arrival_payload(db, session)


@router.post("/api/arrival/client/{session_id}/messages")
def send_client_arrival_message(
    session_id: str, payload: ArrivalMessageInput, request: Request, db: Session = Depends(get_db)
):
    session = _require_arrival_client(request, db, session_id)
    now = datetime.utcnow()
    message = ArrivalChatMessage(id=str(uuid.uuid4()), session_id=session.id, sender="client",
                                 text=payload.text, created_at=now)
    db.add(message)
    session.last_activity_at = now
    db.commit()
    return {"message": _arrival_messages(db, session.id)[-1]}


@router.get("/api/arrival/admin/sessions")
def list_arrival_sessions(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    db.query(ArrivalSession).filter(
        ArrivalSession.expires_at <= now,
        ArrivalSession.status.in_(["invited", "active"]),
    ).update({
        ArrivalSession.status: "expired",
        ArrivalSession.next_alert_at: None,
    }, synchronize_session=False)
    db.commit()
    sessions = db.query(ArrivalSession).order_by(ArrivalSession.last_activity_at.desc()).limit(100).all()
    return [_arrival_payload(db, session, include_messages=False) for session in sessions]


@router.get("/api/arrival/admin/sessions/{session_id}")
def get_admin_arrival_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Arrival session not found.")
    return _arrival_payload(db, session)


@router.post("/api/arrival/admin/sessions/{session_id}/messages")
def send_admin_arrival_message(session_id: str, payload: ArrivalMessageInput, db: Session = Depends(get_db)):
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Arrival session not found.")
    if session.status != "active" or session.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=410, detail="Arrival chat is no longer active.")
    now = datetime.utcnow()
    message = ArrivalChatMessage(id=str(uuid.uuid4()), session_id=session.id, sender="provider",
                                 text=payload.text, created_at=now)
    db.add(message)
    session.last_activity_at = now
    db.commit()
    return {"message": _arrival_messages(db, session.id)[-1]}


@router.post("/api/arrival/admin/sessions/{session_id}/close")
def close_arrival_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Arrival session not found.")
    if session.status not in {"closed", "expired"}:
        session.status = "closed"
        session.closed_at = datetime.utcnow()
        session.next_alert_at = None
        session.last_activity_at = session.closed_at
        db.commit()
    return _arrival_payload(db, session)


@router.post("/api/threads/{thread_id}/arrivals/{session_id}/acknowledge")
def acknowledge_thread_arrival(
    thread_id: str,
    session_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Stop one arrival alert only when its exact account-scoped conversation is opened."""
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if (
        not session
        or session.thread_id != thread.id
        or session.sms_account_key != thread.sms_account_key
    ):
        raise HTTPException(status_code=404, detail="Arrival alert not found for this conversation.")

    now = datetime.utcnow()
    if (
        session.status != "active"
        or session.activated_at is None
        or session.arrival_event_id is None
        or session.expires_at <= now
    ):
        raise HTTPException(status_code=409, detail="This customer has not activated that arrival link.")

    if session.acknowledged_at is None:
        acknowledged_at = now
        updated = db.query(ArrivalSession).filter(
            ArrivalSession.id == session.id,
            ArrivalSession.thread_id == thread.id,
            ArrivalSession.sms_account_key == thread.sms_account_key,
            ArrivalSession.status == "active",
            ArrivalSession.activated_at.isnot(None),
            ArrivalSession.arrival_event_id.isnot(None),
            ArrivalSession.acknowledged_at.is_(None),
            ArrivalSession.expires_at > acknowledged_at,
        ).update({
            ArrivalSession.acknowledged_at: acknowledged_at,
            ArrivalSession.next_alert_at: None,
            ArrivalSession.last_activity_at: acknowledged_at,
        }, synchronize_session=False)
        if updated != 1:
            db.rollback()
            session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).one()
            if session.acknowledged_at is not None:
                return {
                    "status": "acknowledged",
                    "sessionId": session.id,
                    "acknowledgedAt": session.acknowledged_at.isoformat() + "Z",
                }
            raise HTTPException(status_code=409, detail="This arrival alert could not be acknowledged.")
        db.add(ThreadEvent(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"customer-arrival-acknowledged:{session.id}")),
            thread_id=thread.id,
            type="customer-arrival-acknowledged",
            agent_id="user",
            meta=json.dumps({
                "arrival_session_id": session.id,
                "arrival_event_id": session.arrival_event_id,
            }),
            at=acknowledged_at,
        ))
        db.commit()
        session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).one()
        background_tasks.add_task(send_arrival_clear_notifications, session.id)
    return {
        "status": "acknowledged",
        "sessionId": session.id,
        "acknowledgedAt": session.acknowledged_at.isoformat() + "Z" if session.acknowledged_at else None,
    }


__all__ = [
    "router",
    "create_arrival_invite",
    "activate_arrival",
    "get_arrival_invite_status",
    "get_client_arrival_session",
    "send_client_arrival_message",
    "list_arrival_sessions",
    "get_admin_arrival_session",
    "send_admin_arrival_message",
    "close_arrival_session",
    "acknowledge_thread_arrival",
]

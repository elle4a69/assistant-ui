"""Arrival and check-in domain service module.

Handles:
- Customer arrival token generation and validation
- Arrival sessions lifecycle and database bindings
- Web Push notifications and repeated reminder alerts
- Fast-path customer arrival intent detection
- Simulator phone normalization
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

try:
    import mobilemessage_service
except ImportError:
    from backend import mobilemessage_service

# Defensive dual-import fallbacks
try:
    from backend.core.config import (
        DATA_DIR,
        FIRST_CONTACT_ACCOUNT_KEYS,
        TMP_DIR,
    )
    from backend.core.clients import (
        canonical_phone_number,
    )
    from backend.core.database import SessionLocal
    from backend.core.state import _vapid_key_lock
    from backend.models import (
        ArrivalChatMessage,
        ArrivalSession,
        CalendarEvent,
        PushSubscription,
        Thread,
        ThreadEvent,
    )
except ImportError:
    from core.config import (
        DATA_DIR,
        FIRST_CONTACT_ACCOUNT_KEYS,
        TMP_DIR,
    )
    from core.clients import (
        canonical_phone_number,
    )
    from core.database import SessionLocal
    from core.state import _vapid_key_lock
    from models import (
        ArrivalChatMessage,
        ArrivalSession,
        CalendarEvent,
        PushSubscription,
        Thread,
        ThreadEvent,
    )

try:
    from pywebpush import WebPushException, webpush
    WEB_PUSH_AVAILABLE = True
except ImportError:
    WebPushException = Exception
    webpush = None
    WEB_PUSH_AVAILABLE = False

logger = logging.getLogger(__name__)


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


ARRIVAL_NEGATIVE_PATTERNS = (
    r"\bnot (?:there|here) yet\b",
    r"\b(?:have not|haven't|has not|hasn't) arrived\b",
    r"\b(?:when|once|before|after) (?:i|we) (?:arrive|get there)\b",
    r"\b(?:i|we)(?:'m| are| am)? (?:on (?:my|our|the) way|almost there)\b",
    r"\b(?:minutes?|mins?|hours?) away\b",
    r"\b(?:will|should|might|may) (?:be there|arrive)\b",
)
ARRIVAL_POSITIVE_PATTERNS = (
    r"\b(?:i(?:'m| am)|we(?:'re| are)) here\b",
    r"\b(?:i|we)(?:'ve| have)? (?:just )?arrived\b",
    r"\bjust (?:got|made it) here\b",
    r"\b(?:i(?:'m| am)|we(?:'re| are)) (?:at|outside) (?:the )?(?:front )?door\b",
    r"\b(?:i(?:'m| am)|we(?:'re| are)) in (?:the )?(?:waiting room|reception)\b",
    r"\bwaiting (?:outside|out front|downstairs|at (?:the )?(?:front )?door)\b",
    r"\b(?:parked|pulled up) (?:outside|out front)\b",
)

ARRIVAL_ALERT_INTERVAL_SECONDS = 60
ARRIVAL_ALERT_LEASE_SECONDS = 300


def _hash_arrival_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _arrival_booking(db: Session, booking_id: str) -> Optional[CalendarEvent]:
    return db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()


def _arrival_messages(db: Session, session_id: str) -> List[Dict[str, Any]]:
    messages = (
        db.query(ArrivalChatMessage)
        .filter(ArrivalChatMessage.session_id == session_id)
        .order_by(ArrivalChatMessage.created_at.asc(), ArrivalChatMessage.id.asc())
        .all()
    )
    return [
        {
            "id": message.id,
            "sender": message.sender,
            "text": message.text,
            "createdAt": message.created_at.isoformat() + "Z",
        }
        for message in messages
    ]


def _arrival_payload(db: Session, session: ArrivalSession, include_messages: bool = True) -> Dict[str, Any]:
    booking = _arrival_booking(db, session.booking_id)
    payload: Dict[str, Any] = {
        "id": session.id,
        "bookingId": session.booking_id,
        "threadId": session.thread_id,
        "smsAccountKey": session.sms_account_key,
        "arrivalEventId": session.arrival_event_id,
        "status": session.status,
        "expiresAt": session.expires_at.isoformat() + "Z",
        "activatedAt": session.activated_at.isoformat() + "Z" if session.activated_at else None,
        "acknowledgedAt": session.acknowledged_at.isoformat() + "Z" if session.acknowledged_at else None,
        "lastAlertAt": session.last_alert_at.isoformat() + "Z" if session.last_alert_at else None,
        "nextAlertAt": session.next_alert_at.isoformat() + "Z" if session.next_alert_at else None,
        "alertCount": session.alert_count or 0,
        "closedAt": session.closed_at.isoformat() + "Z" if session.closed_at else None,
        "lastActivityAt": session.last_activity_at.isoformat() + "Z",
        "booking": {
            "summary": booking.summary if booking else "Appointment",
            "customerPhone": booking.customer_phone if booking else None,
            "startTime": booking.start_time.isoformat() + "Z" if booking else None,
            "endTime": booking.end_time.isoformat() + "Z" if booking else None,
        },
    }
    if include_messages:
        payload["messages"] = _arrival_messages(db, session.id)
    return payload


def _require_arrival_client(request: Request, db: Session, session_id: str) -> ArrivalSession:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "arrival" or not token:
        raise HTTPException(status_code=401, detail="Arrival session token required.")
    session = db.query(ArrivalSession).filter(
        ArrivalSession.id == session_id,
        or_(
            ArrivalSession.client_token_hash == _hash_arrival_token(token),
            ArrivalSession.invite_token_hash == _hash_arrival_token(token),
        ),
    ).first()
    if not session:
        raise HTTPException(status_code=401, detail="This arrival session is not valid.")
    if session.expires_at <= datetime.utcnow():
        if session.status not in {"closed", "expired"}:
            session.status = "expired"
            db.commit()
        raise HTTPException(status_code=410, detail="This arrival session has expired.")
    if session.status != "active":
        raise HTTPException(status_code=410, detail="This arrival session is closed.")
    return session


def _arrival_public_link(invite_token: str, base_url: Optional[str] = None) -> str:
    if base_url:
        origin = base_url.rstrip("/")
    else:
        configured = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
        fly_app_name = os.getenv("FLY_APP_NAME", "").strip()
        origin = configured or (f"https://{fly_app_name}.fly.dev" if fly_app_name else "http://localhost:5190")
    return f"{origin}/a/{invite_token}"


def _base62_encode(value: int) -> str:
    """Base-62 encoding adapted from the existing fastapi_bookings shortener."""
    if value <= 0:
        raise ValueError("Short-link value must be positive.")
    alphabet = string.digits + string.ascii_letters
    encoded: List[str] = []
    while value:
        value, remainder = divmod(value, len(alphabet))
        encoded.append(alphabet[remainder])
    return "".join(reversed(encoded))


def _new_arrival_short_code() -> str:
    # 96 random bits keeps the private booking credential unguessable while
    # producing a substantially shorter, SMS-friendly base-62 code.
    while True:
        code = _base62_encode(secrets.randbits(96) or 1)
        if len(code) >= 16:
            return code


def _arrival_thread_for_invite(
    db: Session,
    *,
    customer_phone: Optional[str],
    sms_account_key: str,
    thread_id: Optional[str],
    start_time: datetime,
) -> Thread:
    """Resolve one exact account-scoped conversation without crossing SMS lines."""
    if sms_account_key not in FIRST_CONTACT_ACCOUNT_KEYS:
        raise ValueError("A valid SMS account is required for an arrival link.")

    normalized_destination = mobilemessage_service.normalize_sms_destination(customer_phone or "")
    if not normalized_destination:
        raise ValueError("A valid customer phone number is required for an arrival link.")
    canonical_phone = canonical_phone_number(normalized_destination)
    if thread_id:
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if not thread or thread.sms_account_key != sms_account_key:
            raise ValueError("The selected conversation does not belong to that SMS account.")
        if canonical_phone and canonical_phone_number(thread.customer_phone) != canonical_phone:
            raise ValueError("The selected conversation does not belong to that customer.")
        return thread

    finder = _dyn("find_thread_by_phone", None)
    if finder is None:
        try:
            from backend.services.sms_service import find_thread_by_phone as finder
        except ImportError:
            from services.sms_service import find_thread_by_phone as finder

    thread = finder(db, canonical_phone, sms_account_key)
    if thread:
        return thread

    now = datetime.utcnow()
    thread = Thread(
        id=str(uuid.uuid4()),
        customer_phone=canonical_phone,
        sms_account_key=sms_account_key,
        state="resolved",
        priority="medium",
        sla_due_at=start_time.replace(tzinfo=None) + timedelta(hours=24),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.flush()
    return thread


def _issue_arrival_invite(
    db: Session,
    *,
    booking_id: str,
    summary: str,
    customer_phone: Optional[str],
    sms_account_key: str,
    thread_id: Optional[str] = None,
    start_time: datetime,
    end_time: datetime,
) -> tuple[ArrivalSession, str]:
    """Create one account-bound invitation while revoking older booking links."""
    now = datetime.utcnow()
    local_start = start_time.replace(tzinfo=None)
    local_end = end_time.replace(tzinfo=None)
    if local_end <= local_start:
        raise ValueError("Booking end time must be after its start time.")

    thread = _arrival_thread_for_invite(
        db,
        customer_phone=customer_phone,
        sms_account_key=sms_account_key,
        thread_id=thread_id,
        start_time=local_start,
    )
    booking = _arrival_booking(db, booking_id)
    if not booking:
        booking = CalendarEvent(
            id=booking_id, summary=summary, customer_phone=customer_phone,
            sms_account_key=sms_account_key, thread_id=thread.id,
            start_time=local_start, end_time=local_end, status="scheduled", notes="",
        )
        db.add(booking)
    else:
        if booking.sms_account_key and booking.sms_account_key != sms_account_key:
            raise ValueError("This booking is already tied to another SMS line.")
        if booking.thread_id and booking.thread_id != thread.id:
            raise ValueError("This booking is already tied to another SMS conversation.")
        booking.summary = summary
        booking.customer_phone = customer_phone
        booking.sms_account_key = sms_account_key
        booking.thread_id = thread.id
        booking.start_time = local_start
        booking.end_time = local_end

    for old_session in db.query(ArrivalSession).filter(
        ArrivalSession.booking_id == booking_id,
        ArrivalSession.status.in_(["invited", "active"]),
    ).all():
        old_session.status = "closed"
        old_session.closed_at = now
        old_session.next_alert_at = None
        old_session.last_activity_at = now

    invite_token = _new_arrival_short_code()
    expires_at = min(
        max(local_end + timedelta(hours=6), now + timedelta(hours=1)),
        now + timedelta(days=30),
    )
    session = ArrivalSession(
        id=str(uuid.uuid4()), booking_id=booking_id,
        thread_id=thread.id,
        sms_account_key=sms_account_key,
        invite_token_hash=_hash_arrival_token(invite_token), status="invited",
        expires_at=expires_at, created_at=now, last_activity_at=now,
    )
    db.add(session)
    db.flush()
    return session, invite_token


def _bind_legacy_arrival_session(db: Session, session: ArrivalSession) -> bool:
    """Bind historical links only when their account-scoped thread is unambiguous."""
    booking = _arrival_booking(db, session.booking_id)
    canonical_phone = canonical_phone_number(booking.customer_phone if booking else "")

    if session.thread_id:
        thread = db.query(Thread).filter(Thread.id == session.thread_id).first()
        if not thread:
            return False
        if session.sms_account_key and thread.sms_account_key != session.sms_account_key:
            return False
        if canonical_phone and canonical_phone_number(thread.customer_phone) != canonical_phone:
            return False
        session.sms_account_key = thread.sms_account_key
        return True

    if not canonical_phone:
        return False
    matches = [
        thread
        for thread in db.query(Thread).all()
        if canonical_phone_number(thread.customer_phone) == canonical_phone
        and (not session.sms_account_key or thread.sms_account_key == session.sms_account_key)
    ]
    if len(matches) != 1:
        return False
    session.thread_id = matches[0].id
    session.sms_account_key = matches[0].sms_account_key
    return True


def _record_arrival_link_thread_event(
    db: Session,
    session: ArrivalSession,
    at: datetime,
) -> ThreadEvent:
    """Create the one normal-conversation event associated with a link check-in."""
    thread = db.query(Thread).filter(
        Thread.id == session.thread_id,
        Thread.sms_account_key == session.sms_account_key,
    ).first()
    if not thread:
        raise ValueError("The arrival link is not bound to its SMS conversation.")

    if session.arrival_event_id:
        existing = db.query(ThreadEvent).filter(
            ThreadEvent.id == session.arrival_event_id,
            ThreadEvent.thread_id == thread.id,
            ThreadEvent.type == "customer-arrived",
        ).first()
        if existing:
            return existing

    arrival_event = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="customer-arrived",
        agent_id=None,
        meta=json.dumps({
            "arrival_session_id": session.id,
            "booking_id": session.booking_id,
            "detection_method": "arrival-link",
        }),
        at=at,
    )
    db.add(arrival_event)
    db.flush()
    session.arrival_event_id = arrival_event.id
    thread.updated_at = at
    return arrival_event


def _prepare_active_arrival_session(db: Session, session: ArrivalSession, now: datetime) -> bool:
    """Safely attach pre-migration active sessions to the normal conversation alert flow."""
    if session.status != "active" or not session.activated_at or session.expires_at <= now:
        return False
    if not _bind_legacy_arrival_session(db, session):
        return False
    if not session.arrival_event_id:
        _record_arrival_link_thread_event(db, session, session.activated_at)
    if session.acknowledged_at is None and session.next_alert_at is None:
        session.next_alert_at = now
    session.last_activity_at = max(session.last_activity_at or now, session.activated_at)
    return True


def _ensure_persistent_vapid_keypair() -> tuple[Optional[str], str]:
    """Generate the app's signing identity once and retain it on the existing data volume."""
    data_dir = _dyn("DATA_DIR", DATA_DIR)
    private_path = os.path.join(data_dir, "vapid_private.pem")
    public_path = os.path.join(data_dir, "vapid_public.txt")
    vapid_lock = _dyn("_vapid_key_lock", _vapid_key_lock)
    with vapid_lock:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            if os.path.exists(private_path):
                private_key = serialization.load_pem_private_key(Path(private_path).read_bytes(), password=None)
            else:
                private_key = ec.generate_private_key(ec.SECP256R1())
                private_pem = private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                temporary_path = f"{private_path}.{uuid.uuid4().hex}.tmp"
                Path(temporary_path).write_bytes(private_pem)
                try:
                    os.chmod(temporary_path, 0o600)
                except OSError:
                    pass
                os.replace(temporary_path, private_path)

            public_raw = private_key.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
            public_key = base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("=")
            if not os.path.exists(public_path) or Path(public_path).read_text(encoding="utf-8").strip() != public_key:
                Path(public_path).write_text(public_key, encoding="utf-8")
            return private_path, public_key
        except Exception:
            logger.exception("Could not initialize the persistent Web Push signing key")
            return None, ""


def _vapid_private_key() -> Optional[str]:
    """Return a pywebpush-compatible PEM path without persisting a secret in source."""
    configured_path = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    if configured_path:
        return configured_path
    encoded = os.getenv("VAPID_PRIVATE_KEY_B64", "").strip()
    if encoded:
        tmp_dir = _dyn("TMP_DIR", TMP_DIR)
        key_path = os.path.join(tmp_dir, "vapid_private.pem")
        try:
            decoded = base64.b64decode(encoded, validate=True)
            if b"PRIVATE KEY" not in decoded:
                return None
            if not os.path.exists(key_path) or Path(key_path).read_bytes() != decoded:
                Path(key_path).write_bytes(decoded)
                try:
                    os.chmod(key_path, 0o600)
                except OSError:
                    pass
            return key_path
        except (ValueError, OSError):
            return None
    return _ensure_persistent_vapid_keypair()[0]


def _vapid_public_key() -> str:
    configured = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    return configured or _ensure_persistent_vapid_keypair()[1]


def _push_configured() -> bool:
    vapid_key_fn = _dyn("_vapid_private_key", _vapid_private_key)
    return bool(
        _dyn("WEB_PUSH_AVAILABLE", WEB_PUSH_AVAILABLE)
        and _vapid_public_key()
        and (vapid_key_fn() if callable(vapid_key_fn) else None)
    )


def send_arrival_push_notifications(session_id: str, clear: bool = False) -> None:
    """Best-effort delivery: an alert failure must never undo an arrival."""
    wp = _dyn("webpush", webpush)
    session_factory = _dyn("SessionLocal", SessionLocal)
    db = session_factory()
    try:
        session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
        if not session:
            return
        if clear:
            remaining_count = db.query(ArrivalSession).filter(
                ArrivalSession.status == "active",
                ArrivalSession.acknowledged_at.is_(None),
                ArrivalSession.expires_at > datetime.utcnow(),
            ).count()
            payload = json.dumps({
                "type": "customer-arrival-cleared",
                "tag": f"arrival-{session.id}",
                "sessionId": session.id,
                "remainingCount": remaining_count,
            })
        else:
            if (
                session.status != "active"
                or session.acknowledged_at is not None
                or session.expires_at <= datetime.utcnow()
            ):
                return
            destination = (
                f"/chat?thread={session.thread_id}&arrival={session.id}"
                if session.thread_id
                else f"/arrivals?session={session.id}"
            )
            payload = json.dumps({
                "type": "customer-arrival",
                "title": "Customer has arrived",
                "body": "A customer is waiting. Tap to open the conversation.",
                "url": destination,
                "tag": f"arrival-{session.id}",
                "sessionId": session.id,
                "threadId": session.thread_id,
            })
            # This is called for the initial activation and later reminder
            # checks.  The durable per-session key means ntfy sees one
            # meaningful arrival alert while Web Push retains its existing
            # repeating-alarm behaviour.
            notify_fn = _dyn("send_notification", None)
            if notify_fn is None:
                try:
                    from backend.services.notification_service import (
                        build_notification_url,
                        send_notification as notify_fn,
                    )
                except ImportError:
                    from services.notification_service import (
                        build_notification_url,
                        send_notification as notify_fn,
                    )
            else:
                build_notification_url = _dyn("build_notification_url", None)
            if callable(notify_fn):
                booking = _arrival_booking(db, session.booking_id)
                label = (booking.summary if booking else "Customer")[:160]
                line_label = "Line 2" if session.sms_account_key == "secondary" else "Line 1"
                click_url = (
                    build_notification_url("/chat", thread=session.thread_id)
                    if callable(build_notification_url) and session.thread_id
                    else build_notification_url("/arrivals") if callable(build_notification_url)
                    else destination
                )
                notify_fn(
                    notification_type="customer_arrival",
                    title="Customer Arrived",
                    message=f"{label}\n{line_label} · Customer is waiting.",
                    click_url=click_url,
                    priority=5,
                    dedupe_key=f"customer-arrival:{session.id}",
                    metadata={"arrival_session_id": session.id},
                )

        # ntfy is independent of browser Web Push. A missing VAPID key must
        # not suppress the owner notification channel.
        if not _push_configured() or wp is None:
            return
        vapid_key_fn = _dyn("_vapid_private_key", _vapid_private_key)
        private_key = vapid_key_fn() if callable(vapid_key_fn) else None
        vapid_contact = os.getenv("VAPID_CONTACT", "mailto:admin@assistant-ui-hub.fly.dev")
        now = datetime.utcnow()
        for subscription in db.query(PushSubscription).filter(PushSubscription.active.is_(True)).all():
            if not clear:
                db.refresh(session)
                if session.acknowledged_at is not None or session.status != "active":
                    break
            try:
                wp(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=payload,
                    vapid_private_key=private_key,
                    vapid_claims={"sub": vapid_contact},
                    timeout=10,
                )
                subscription.failure_count = 0
                subscription.last_success_at = now
                subscription.updated_at = now
            except Exception as exc:
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                if status_code in {404, 410}:
                    subscription.active = False
                else:
                    subscription.failure_count = (subscription.failure_count or 0) + 1
                subscription.updated_at = now
                logger.warning("Web Push delivery failed (status=%s)", status_code or "unknown")
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Arrival Web Push dispatch failed")
    finally:
        db.close()


def send_arrival_clear_notifications(session_id: str) -> None:
    send_arrival_push_notifications(session_id, clear=True)


def process_due_arrival_alerts() -> int:
    """Claim and dispatch each due reminder once across concurrent workers."""
    now = datetime.utcnow()
    session_factory = _dyn("SessionLocal", SessionLocal)
    db = session_factory()
    dispatched = 0
    try:
        db.query(ArrivalSession).filter(
            ArrivalSession.status == "active",
            ArrivalSession.expires_at <= now,
        ).update({
            ArrivalSession.status: "expired",
            ArrivalSession.next_alert_at: None,
        }, synchronize_session=False)
        db.commit()

        legacy_active_sessions = db.query(ArrivalSession).filter(
            ArrivalSession.status == "active",
            ArrivalSession.activated_at.isnot(None),
            ArrivalSession.acknowledged_at.is_(None),
            ArrivalSession.expires_at > now,
            or_(
                ArrivalSession.thread_id.is_(None),
                ArrivalSession.sms_account_key.is_(None),
                ArrivalSession.arrival_event_id.is_(None),
                ArrivalSession.next_alert_at.is_(None),
            ),
        ).all()
        for legacy_session in legacy_active_sessions:
            _prepare_active_arrival_session(db, legacy_session, now)
        db.commit()

        due_ids = [
            row.id
            for row in db.query(ArrivalSession.id).join(
                Thread, Thread.id == ArrivalSession.thread_id,
            ).filter(
                ArrivalSession.status == "active",
                ArrivalSession.acknowledged_at.is_(None),
                ArrivalSession.next_alert_at.isnot(None),
                ArrivalSession.next_alert_at <= now,
                ArrivalSession.expires_at > now,
                ArrivalSession.sms_account_key == Thread.sms_account_key,
            ).order_by(ArrivalSession.next_alert_at.asc()).limit(100).all()
        ]
        for session_id in due_ids:
            claim_time = datetime.utcnow()
            lease_until = claim_time + timedelta(seconds=ARRIVAL_ALERT_LEASE_SECONDS)
            claimed = db.query(ArrivalSession).filter(
                ArrivalSession.id == session_id,
                ArrivalSession.status == "active",
                ArrivalSession.acknowledged_at.is_(None),
                ArrivalSession.next_alert_at.isnot(None),
                ArrivalSession.next_alert_at <= claim_time,
                ArrivalSession.expires_at > claim_time,
            ).update({
                ArrivalSession.next_alert_at: lease_until,
            }, synchronize_session=False)
            db.commit()
            if claimed != 1:
                continue
            send_push_fn = _dyn("send_arrival_push_notifications", send_arrival_push_notifications)
            send_push_fn(session_id)
            completed_at = datetime.utcnow()
            db.query(ArrivalSession).filter(
                ArrivalSession.id == session_id,
                ArrivalSession.status == "active",
                ArrivalSession.acknowledged_at.is_(None),
                ArrivalSession.next_alert_at == lease_until,
                ArrivalSession.expires_at > completed_at,
            ).update({
                ArrivalSession.last_alert_at: completed_at,
                ArrivalSession.next_alert_at: completed_at + timedelta(seconds=ARRIVAL_ALERT_INTERVAL_SECONDS),
                ArrivalSession.alert_count: ArrivalSession.alert_count + 1,
                ArrivalSession.last_activity_at: completed_at,
            }, synchronize_session=False)
            db.commit()
            dispatched += 1
        return dispatched
    except Exception:
        db.rollback()
        logger.exception("Repeated customer-arrival alert failed")
        return dispatched
    finally:
        db.close()


async def arrival_alert_worker() -> None:
    while True:
        await asyncio.to_thread(process_due_arrival_alerts)
        await asyncio.sleep(5)


def is_clear_customer_arrival(message: str) -> bool:
    normalized = " ".join((message or "").casefold().replace("’", "'").split())
    if not normalized or any(re.search(pattern, normalized) for pattern in ARRIVAL_NEGATIVE_PATTERNS):
        return False
    return any(re.search(pattern, normalized) for pattern in ARRIVAL_POSITIVE_PATTERNS)


ARRIVAL_AUTO_REPLY_BLOCK_WINDOW = timedelta(hours=2)


def customer_arrival_has_been_recorded(db: Session, thread_id: str) -> bool:
    """Return whether an arrival blocks automated replies for the next two hours.

    The window protects the active arrival and appointment conversation without
    permanently preventing a returning customer from booking again later.
    """
    cutoff = datetime.utcnow() - ARRIVAL_AUTO_REPLY_BLOCK_WINDOW
    return db.query(ThreadEvent.id).filter(
        ThreadEvent.thread_id == thread_id,
        ThreadEvent.type == "customer-arrived",
        ThreadEvent.at >= cutoff,
    ).first() is not None


def record_customer_arrival_event(
    db: Session,
    thread: Thread,
    source_message_id: str,
    detection_method: str,
) -> bool:
    marker = source_message_id or ""
    existing_events = db.query(ThreadEvent).filter(
        ThreadEvent.thread_id == thread.id,
        ThreadEvent.type == "customer-arrived",
    ).all()
    for event in existing_events:
        try:
            event_meta = json.loads(event.meta or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if marker and event_meta.get("source_message_id") == marker:
            return False

    db.add(ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="customer-arrived",
        agent_id=None,
        meta=json.dumps({
            "source_message_id": marker or None,
            "detection_method": detection_method,
        }),
        at=datetime.utcnow(),
    ))
    return True


def normalize_simulator_customer_phone(phone: str) -> str:
    """Apply the SMS transport's phone rules and return the app's canonical form."""
    normalized = mobilemessage_service.normalize_sms_destination(phone)
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail="Customer phone must be a valid Australian mobile or E.164 phone number.",
        )
    return f"+{normalized}"


__all__ = [
    "_hash_arrival_token",
    "_arrival_booking",
    "_arrival_messages",
    "_arrival_payload",
    "_arrival_public_link",
    "_base62_encode",
    "_new_arrival_short_code",
    "_arrival_thread_for_invite",
    "_issue_arrival_invite",
    "_prepare_active_arrival_session",
    "_bind_legacy_arrival_session",
    "_record_arrival_link_thread_event",
    "_require_arrival_client",
    "is_clear_customer_arrival",
    "customer_arrival_has_been_recorded",
    "record_customer_arrival_event",
    "process_due_arrival_alerts",
    "arrival_alert_worker",
    "send_arrival_push_notifications",
    "send_arrival_clear_notifications",
    "normalize_simulator_customer_phone",
    "ARRIVAL_NEGATIVE_PATTERNS",
    "ARRIVAL_POSITIVE_PATTERNS",
    "ARRIVAL_ALERT_INTERVAL_SECONDS",
    "ARRIVAL_ALERT_LEASE_SECONDS",
    "ARRIVAL_AUTO_REPLY_BLOCK_WINDOW",
    "_ensure_persistent_vapid_keypair",
    "_vapid_private_key",
    "_vapid_public_key",
    "_push_configured",
    "WEB_PUSH_AVAILABLE",
    "webpush",
]

"""Booking domain service module.

Handles:
- Slot verification and business working hours checks
- Conversational booking proposal creation and validation
- Conversational booking confirmation with live calendar re-check
- Calendar read-only snapshots and guidance generation
- Pre-write race prevention and audit event logging
- Booking reminder background worker and persistence
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

try:
    import mobilemessage_service
except ImportError:
    from backend import mobilemessage_service

# Defensive dual-import fallbacks
try:
    from backend.core.config import (
        AUDIT_SCHEMA_VERSION,
        AVAILABILITY_CLAIM_RE,
        AVAILABILITY_REQUEST_RE,
        BOOKING_LOCAL_TIMEZONE,
        BOOKING_REMINDER_CONFIG_PATH,
        BOOKING_REMINDER_LOCK,
        BOOKING_REMINDER_SENT_PATH,
        DATA_DIR,
        DAY_NAMES,
        DEFAULT_BOOKING_REMINDER_TEMPLATE,
        DEFAULT_WORKING_HOURS,
        FIRST_CONTACT_ACCOUNT_KEYS,
        PROMPTS_DIR,
        WORKING_HOURS_PATH,
    )
    from backend.core.clients import (
        GoogleCalendarService,
        calendar_service,
        canonical_phone_number,
        resolve_provider_context,
    )
    from backend.core.database import SessionLocal
    from backend.models import (
        CalendarEvent,
        Message,
        Thread,
        ThreadEvent,
    )
except ImportError:
    from core.config import (
        AUDIT_SCHEMA_VERSION,
        AVAILABILITY_CLAIM_RE,
        AVAILABILITY_REQUEST_RE,
        BOOKING_LOCAL_TIMEZONE,
        BOOKING_REMINDER_CONFIG_PATH,
        BOOKING_REMINDER_LOCK,
        BOOKING_REMINDER_SENT_PATH,
        DATA_DIR,
        DAY_NAMES,
        DEFAULT_BOOKING_REMINDER_TEMPLATE,
        DEFAULT_WORKING_HOURS,
        FIRST_CONTACT_ACCOUNT_KEYS,
        PROMPTS_DIR,
        WORKING_HOURS_PATH,
    )
    from core.clients import (
        GoogleCalendarService,
        calendar_service,
        canonical_phone_number,
        resolve_provider_context,
    )
    from core.database import SessionLocal
    from models import (
        CalendarEvent,
        Message,
        Thread,
        ThreadEvent,
    )

logger = logging.getLogger(__name__)

BOOKING_PROVIDERS = {
    "tori": {"name": "Tori", "sms_account_key": "primary"},
    "anonymous": {"name": "Anonymous", "sms_account_key": "secondary"},
}


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def current_business_time() -> datetime:
    return datetime.now(ZoneInfo("Australia/Hobart"))


def parse_business_datetime(value: str) -> datetime:
    """Parse an ISO timestamp and return the same instant in Hobart local time."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    business_tz = ZoneInfo("Australia/Hobart")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=business_tz)
    return parsed.astimezone(business_tz)


def _audit_iso(value: Any, timezone_name: str = "Australia/Hobart") -> Optional[str]:
    """Normalize an audit datetime without retaining the original input text."""
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        zone = ZoneInfo(timezone_name)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone).isoformat()
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None


def _add_structured_thread_event(
    db: Session,
    thread: Thread,
    event_type: str,
    audit: Dict[str, Any],
    **details: Any,
) -> ThreadEvent:
    """Persist allowlisted operational facts only; callers never pass free-form content."""
    meta = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "correlation_id": audit.get("correlation_id"),
        "source_message_id": audit.get("source_message_id"),
        "timezone": audit.get("timezone", "Australia/Hobart"),
        "sms_line": thread.sms_account_key,
        **details,
    }
    event_row = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type=event_type,
        agent_id="system",
        meta=json.dumps(meta, separators=(",", ":"), sort_keys=True),
        at=datetime.utcnow(),
    )
    db.add(event_row)
    return event_row


def load_working_hours() -> List[Dict[str, Any]]:
    working_hours_path = _dyn("WORKING_HOURS_PATH", WORKING_HOURS_PATH)
    if os.path.exists(working_hours_path):
        try:
            with open(working_hours_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_WORKING_HOURS


def load_booking_services() -> List[Dict[str, Any]]:
    """Load both line catalogues for booking infrastructure, never AI context."""
    loader = _dyn("load_all_line_services", None)
    if callable(loader):
        return loader()
    return []


def get_service_for_booking(service_id: str, account_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve a service from the current Settings catalogue at decision time."""
    if account_key in FIRST_CONTACT_ACCOUNT_KEYS:
        line_loader = _dyn("load_line_services", None)
        services = line_loader(account_key) if callable(line_loader) else []
    else:
        all_loader = _dyn("load_all_line_services", None)
        services = all_loader() if callable(all_loader) else []
    return next((
        service for service in services
        if isinstance(service, dict) and service.get("id") == service_id
    ), None)


def is_explicit_booking_confirmation(message: str) -> bool:
    """Accept a short, unambiguous confirmation of an already-presented proposal."""
    normalized = re.sub(
        r"[^a-z0-9' ]+",
        " ",
        (message or "").casefold().replace("’", "'"),
    )
    normalized = " ".join(normalized.split())
    return bool(re.fullmatch(
        r"(?:yes|yep|yeah|correct|confirmed?|go ahead|book it|please book it|"
        r"yes please|yes that's correct|yes that is correct|that's correct|that is correct|"
        r"yes confirm it|confirm it please|yep looks good|yep looks good to me|"
        r"yes looks good|yes looks good to me|looks good|looks good to me)",
        normalized,
    ))


def is_explicit_booking_rejection(message: str) -> bool:
    normalized = re.sub(
        r"[^a-z0-9' ]+",
        " ",
        (message or "").casefold().replace("’", "'"),
    )
    normalized = " ".join(normalized.split())
    return bool(re.fullmatch(
        r"(?:no|no thanks|cancel|cancel it|don't book it|do not book it|"
        r"that's wrong|that is wrong|not correct)",
        normalized,
    ))


def asks_for_secondary_booking_confirmation(message: str) -> bool:
    """Reject the artificial extra approval step after booking details are complete."""
    normalized = re.sub(
        r"[^a-z0-9' ]+",
        " ",
        (message or "").casefold().replace("’", "'"),
    )
    normalized = " ".join(normalized.split())
    return any(re.search(pattern, normalized) for pattern in (
        r"\b(?:reply|respond|say) (?:with )?(?:yes|yep|yeah)\b",
        r"\b(?:is|are) (?:that|those|these|the details) (?:all )?(?:correct|right|okay|ok)\b",
        r"\b(?:please |just )?confirm (?:that|those|these|the|your) details\b",
        r"\b(?:would you like|do you want|want) me to (?:book|lock) (?:that|it) (?:in)?\b",
        r"\bshall i (?:book|lock) (?:that|it) (?:in)?\b",
        r"\b(?:if|once|when) (?:that is|that's|those are|the details are) "
        r"(?:correct|right|okay|ok)\b",
    ))


def booking_availability_error(start: datetime, duration: int, account_key: str = "primary") -> Optional[str]:
    """Return a customer-safe reason when an exact proposed slot cannot be booked."""
    now_fn = _dyn("current_business_time", current_business_time)
    now = now_fn()
    end = start + timedelta(minutes=duration)
    if start < now:
        return "Bookings cannot be made in the past."
    if start > now + timedelta(days=180):
        return "Bookings can only be made up to 180 days ahead."

    hours_loader = _dyn("load_working_hours", load_working_hours)
    working_hours = {
        entry["day"]: entry for entry in hours_loader()
        if isinstance(entry, dict) and entry.get("day")
    }
    day_config = working_hours.get(DAY_NAMES[start.weekday()])
    if not day_config or not day_config.get("enabled", False):
        return "The business is closed at that time."
    try:
        open_hour, open_minute = map(int, day_config["open"].split(":"))
        close_hour, close_minute = map(int, day_config["close"].split(":"))
    except (KeyError, TypeError, ValueError):
        return "The working hours for that day are not configured correctly."

    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if (
        start.date() != end.date()
        or start_minutes < open_hour * 60 + open_minute
        or end_minutes > close_hour * 60 + close_minute
    ):
        return "The full appointment does not fit within working hours."

    cs = _dyn("calendar_service", calendar_service)
    authoritative_loader = getattr(cs, "get_busy_slots_for_account", None)
    if not callable(authoritative_loader):
        # Non-production adapters are used by the isolated test/simulation
        # harness. The real calendar service always exposes the scoped method.
        if isinstance(cs, GoogleCalendarService):
            return "Live calendar availability could not be verified. No booking was made."
        authoritative_loader = getattr(cs, "get_busy_slots_authoritative", cs.get_busy_slots)
        try:
            busy_slots = authoritative_loader(start, end)
        except (OSError, RuntimeError):
            return "Live calendar availability could not be verified. No booking was made."
    else:
        try:
            busy_slots = authoritative_loader(start, end, account_key, require_authoritative=True)
        except (OSError, RuntimeError):
            return "Live calendar availability could not be verified. No booking was made."
    if any(
        start < busy["end"] and end > busy["start"]
        for busy in busy_slots
    ):
        return "That time overlaps another booking."
    return None


def propose_conversational_booking(
    thread: Thread,
    *,
    service_id: str,
    start_time: str,
    customer_name: str,
    notes: Optional[str],
) -> Dict[str, Any]:
    """Validate and save a proposal; this function never creates a booking."""
    service = get_service_for_booking((service_id or "").strip(), thread.sms_account_key)
    if not service:
        return {"status": "rejected", "reason": "That service is not available."}
    clean_name = (customer_name or "").strip()[:120]
    if not clean_name:
        return {"status": "rejected", "reason": "The customer's name is still required."}
    try:
        start = parse_business_datetime(start_time)
        duration = max(1, min(1440, int(service.get("duration", 60))))
    except (TypeError, ValueError):
        return {"status": "rejected", "reason": "The appointment time or duration is invalid."}

    avail_fn = _dyn("booking_availability_error", booking_availability_error)
    availability_err = avail_fn(start, duration, thread.sms_account_key)
    if availability_err:
        return {"status": "rejected", "reason": availability_err}

    proposal = {
        "service_id": service["id"],
        "service_name": str(service.get("name") or "Appointment"),
        "duration": duration,
        "price": int(service.get("price", 0) or 0),
        "show_duration": service.get("showDuration", True) is not False,
        "start_time": start.isoformat(),
        "customer_name": clean_name,
        "customer_phone": canonical_phone_number(thread.customer_phone),
        "notes": (notes or "").strip()[:1000],
        "created_at": datetime.utcnow().isoformat(),
    }
    return {
        "status": "awaiting_confirmation",
        "proposal": proposal,
        "instruction": (
            "The application will immediately re-check the live calendar before creating this booking. "
            "After confirmed, send a short natural confirmation without recapping the appointment."
        ),
    }


def confirm_conversational_booking(
    db: Session,
    thread: Thread,
    customer_confirmation: str,
    *,
    proposal_override: Optional[Dict[str, Any]] = None,
    require_customer_confirmation: bool = True,
    send_confirmation: bool = True,
    audit: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], bool]:
    """Create a booking from a validated proposal and re-check live availability."""
    checker = _dyn("is_explicit_booking_confirmation", is_explicit_booking_confirmation)
    if require_customer_confirmation and not checker(customer_confirmation):
        return {
            "status": "rejected",
            "reason": "The customer's latest message was not an explicit confirmation.",
        }, False
    try:
        proposal = dict(proposal_override or json.loads(thread.pending_booking or ""))
        service = get_service_for_booking(str(proposal.get("service_id") or ""), thread.sms_account_key)
        if not service:
            raise ValueError("service no longer exists in this account catalogue")
        # A stored proposal is chronological booking state, but prices and
        # durations are not. Re-resolve those live Settings facts immediately
        # before the calendar decision.
        proposal["service_name"] = str(service.get("name") or "Appointment")
        proposal["duration"] = max(1, min(1440, int(service.get("duration", 60))))
        proposal["price"] = int(service.get("price", 0) or 0)
        proposal["show_duration"] = service.get("showDuration", True) is not False
        proposed_at = datetime.fromisoformat(proposal["created_at"])
        start = parse_business_datetime(proposal["start_time"])
        duration = int(proposal["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        thread.pending_booking = None
        return {"status": "rejected", "reason": "There is no valid booking proposal to confirm."}, False

    if datetime.utcnow() - proposed_at > timedelta(hours=2):
        thread.pending_booking = None
        return {
            "status": "rejected",
            "reason": "The proposed booking expired. Check availability and present it again.",
        }, False

    audit = audit or {}
    normalized_start = _audit_iso(start, audit.get("timezone", "Australia/Hobart"))
    booking_inputs = {
        "service_id": str(proposal.get("service_id") or ""),
        "provider_binding": "secondary-line-provider" if thread.sms_account_key == "secondary" else "primary-line-provider",
        "calendar_binding": "business-calendar",
        "requested_slot": normalized_start,
        "duration_minutes": duration,
        "pending_before": bool(thread.pending_booking or proposal_override),
    }
    attempted_at = time.monotonic()
    if audit:
        _add_structured_thread_event(
            db, thread, "booking_attempted", audit,
            **booking_inputs,
            status_code="attempted",
        )

    cs = _dyn("calendar_service", calendar_service)
    existing = cs.get_customer_bookings(
        thread.customer_phone,
        start - timedelta(minutes=1),
        start + timedelta(minutes=duration + 1),
        thread.sms_account_key,
        db=db,
    )
    if any(item["start"] == start for item in existing):
        thread.pending_booking = None
        if audit:
            existing_id = next(
                (str(item.get("id")) for item in existing if item["start"] == start and item.get("id")),
                None,
            )
            _add_structured_thread_event(
                db, thread, "booking_succeeded", audit,
                **booking_inputs,
                status_code="already_confirmed",
                internal_booking_id=existing_id,
                external_booking_id=None,
                pending_after=False,
                elapsed_ms=round((time.monotonic() - attempted_at) * 1000),
            )
        return {"status": "already_confirmed", "booking": proposal}, True

    # Never confirm from the short availability cache. Fetch the calendar again
    # immediately before the write so a newly occupied time is caught.
    if hasattr(cs, "_cache"):
        cs._cache.clear()
    lookup_id = str(uuid.uuid4())
    lookup_started = time.monotonic()
    if audit:
        _add_structured_thread_event(
            db, thread, "availability_lookup_started", audit,
            lookup_id=lookup_id,
            tool="booking_prewrite_recheck",
            requested_slot=normalized_start,
            service_id=booking_inputs["service_id"],
            provider_binding=booking_inputs["provider_binding"],
            calendar_binding=booking_inputs["calendar_binding"],
            lookup_source="legacy_calendar",
            freshness="authoritative_live",
            cache_status="cleared_and_bypassed",
            pending_state={"proposal": booking_inputs["pending_before"], "accepted_slot": normalized_start},
            policy_inputs=_availability_policy_inputs(normalized_start[:10] if normalized_start else None),
        )
    availability_error_fn = _dyn("booking_availability_error", booking_availability_error)
    availability_error = availability_error_fn(start, duration, thread.sms_account_key)
    lookup_failed = bool(
        availability_error
        and availability_error.startswith("Live calendar availability could not be verified")
    )
    if audit:
        lookup_details = {
            "lookup_id": lookup_id,
            "tool": "booking_prewrite_recheck",
            "requested_slot": normalized_start,
            "service_id": booking_inputs["service_id"],
            "provider_binding": booking_inputs["provider_binding"],
            "calendar_binding": booking_inputs["calendar_binding"],
            "lookup_source": "legacy_calendar",
            "freshness": "authoritative_live",
            "cache_status": "cleared_and_bypassed",
            "pending_state": {"proposal": booking_inputs["pending_before"], "accepted_slot": normalized_start},
            "policy_inputs": _availability_policy_inputs(normalized_start[:10] if normalized_start else None),
            "elapsed_ms": round((time.monotonic() - lookup_started) * 1000),
        }
        if lookup_failed:
            _add_structured_thread_event(
                db, thread, "availability_lookup_failed", audit,
                **lookup_details,
                status_code="calendar_unavailable",
                exception_classification="expected_provider_error",
            )
        else:
            _add_structured_thread_event(
                db, thread, "availability_lookup_completed", audit,
                **lookup_details,
                result={
                    "available": not bool(availability_error),
                    "slot_count": 1 if not availability_error else 0,
                    "candidate_range": {
                        "first_start": normalized_start if not availability_error else None,
                        "last_end": _audit_iso(start + timedelta(minutes=duration), audit.get("timezone", "Australia/Hobart")) if not availability_error else None,
                        "returned_count": 1 if not availability_error else 0,
                        "bounded": True,
                    },
                    "conflict": {
                        "classification": (
                            "calendar_conflict" if availability_error and "overlaps" in availability_error
                            else "policy_rejected" if availability_error else "none_observed"
                        ),
                        "ids": [],
                    },
                },
            )
    if availability_error:
        thread.pending_booking = None
        if audit:
            conflict = "calendar_conflict" if "overlaps" in availability_error else "policy_rejected"
            _add_structured_thread_event(
                db, thread, "booking_failed" if lookup_failed else "booking_conflict", audit,
                **booking_inputs,
                status_code="calendar_unavailable" if lookup_failed else conflict,
                exception_classification="expected_provider_error" if lookup_failed else None,
                conflict={"classification": "unknown_due_to_lookup_failure" if lookup_failed else conflict, "ids": []},
                pending_after=False,
                elapsed_ms=round((time.monotonic() - attempted_at) * 1000),
            )
        return {"status": "rejected", "reason": availability_error}, False

    end = start + timedelta(minutes=duration)
    booking_provider_name = "Anonymous" if thread.sms_account_key == "secondary" else "Tori"
    booking_summary = (
        f"{proposal['customer_name']} - {proposal['service_name']} "
        f"({booking_provider_name})"
    )
    booking_id = cs.create_booking(
        summary=booking_summary, start=start, end=end,
        customer_phone=thread.customer_phone, sms_account_key=thread.sms_account_key,
    )
    if not booking_id:
        if audit:
            _add_structured_thread_event(
                db, thread, "booking_failed", audit,
                **booking_inputs,
                status_code="provider_write_rejected",
                exception_classification="provider_rejected",
                internal_booking_id=None,
                external_booking_id=None,
                pending_after=bool(thread.pending_booking),
                elapsed_ms=round((time.monotonic() - attempted_at) * 1000),
            )
        return {"status": "failed", "reason": "The calendar did not accept the booking."}, False

    # Issue arrival invite
    issue_invite = _dyn("_issue_arrival_invite", None)
    if issue_invite is None:
        try:
            from backend.services.arrival_service import _issue_arrival_invite as issue_invite
        except ImportError:
            from services.arrival_service import _issue_arrival_invite as issue_invite

    arrival_session, arrival_token = issue_invite(
        db,
        booking_id=str(booking_id),
        summary=booking_summary,
        customer_phone=thread.customer_phone,
        sms_account_key=thread.sms_account_key,
        thread_id=thread.id,
        start_time=start,
        end_time=end,
    )

    arrival_booking_fn = _dyn("_arrival_booking", None)
    if arrival_booking_fn is None:
        try:
            from backend.services.arrival_service import _arrival_booking as arrival_booking_fn
        except ImportError:
            from services.arrival_service import _arrival_booking as arrival_booking_fn
    local_booking = arrival_booking_fn(db, str(booking_id))
    if local_booking:
        local_booking.amount = int(proposal.get("price", 0) or 0)

    arrival_link_fn = _dyn("_arrival_public_link", None)
    if arrival_link_fn is None:
        try:
            from backend.services.arrival_service import _arrival_public_link as arrival_link_fn
        except ImportError:
            from services.arrival_service import _arrival_public_link as arrival_link_fn

    proposal["arrival_link"] = arrival_link_fn(arrival_token)
    proposal["arrival_session_id"] = arrival_session.id

    if send_confirmation:
        prompts_dir = _dyn("PROMPTS_DIR", PROMPTS_DIR)
        template_path = os.path.join(prompts_dir, "sms_confirmation_template.txt")
        template = (
            "Hi {name}, your booking for {service} on {time} is confirmed!\n\n"
            "When you arrive, tap: {arrival_link}"
        )
        if os.path.exists(template_path):
            try:
                with open(template_path, "r", encoding="utf-8") as handle:
                    template = handle.read()
            except OSError:
                pass
        provider_name = "Anonymous" if thread.sms_account_key == "secondary" else "Tori"

        render_fn = _dyn("render_template_variables", None)
        if render_fn is None:
            try:
                from backend.knowledge import render_template_variables as render_fn
            except ImportError:
                from knowledge import render_template_variables as render_fn

        get_vars_fn = _dyn("get_business_variable_values", None)
        biz_vars = get_vars_fn() if callable(get_vars_fn) else {}

        confirmation_text = render_fn(template, {
            **biz_vars,
            "name": proposal["customer_name"],
            "service": proposal["service_name"],
            "provider": provider_name,
            "time": start.strftime("%A, %b %d at %I:%M %p"),
            "arrival_link": proposal["arrival_link"],
        })
        if "{arrival_link}" not in template:
            confirmation_text = f"{confirmation_text.rstrip()}\n\nWhen you arrive, tap: {proposal['arrival_link']}"
        confirmation_message = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="agent",
            text=confirmation_text,
            at=datetime.utcnow(),
        )
        dispatch_result = mobilemessage_service.send_sms(
            thread.customer_phone,
            confirmation_text,
            idempotency_key=confirmation_message.id,
            account_key=thread.sms_account_key,
        )
        delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
        if dispatch_result.get("status") == "skipped" or (
            delivery_failure and "skipped" in str(delivery_failure).lower()
        ):
            delivery_failure = None
        if delivery_failure:
            confirmation_message.role = "draft"
            thread.state = "needs-review"
        db.add(confirmation_message)
        audit["generated_reply_message_id"] = confirmation_message.id
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="booking-confirmation-delivery-failed" if delivery_failure else "booking-confirmation-sent",
            agent_id="system",
            meta=json.dumps({
                "booking_id": str(booking_id),
                **({"reason": delivery_failure[:500]} if delivery_failure else {}),
            }),
            at=datetime.utcnow(),
        ))
        proposal["booking_confirmation_handled"] = True
        proposal["booking_confirmation_sent"] = not bool(delivery_failure)

    thread.pending_booking = None
    thread.pending_slots = None
    if audit:
        _add_structured_thread_event(
            db, thread, "booking_succeeded", audit,
            **booking_inputs,
            status_code="created",
            internal_booking_id=str(booking_id),
            external_booking_id=str(booking_id) if getattr(cs, "service", None) else None,
            pending_after=False,
            elapsed_ms=round((time.monotonic() - attempted_at) * 1000),
        )
    return {"status": "confirmed", "booking": proposal}, True


def _availability_policy_inputs(local_date: Optional[str]) -> Dict[str, Any]:
    selected = None
    try:
        if local_date:
            weekday = datetime.fromisoformat(local_date).strftime("%A")
            hours_loader = _dyn("load_working_hours", load_working_hours)
            selected = next(
                (item for item in hours_loader() if item.get("day") == weekday),
                None,
            )
    except (TypeError, ValueError):
        selected = None
    return {
        "working_hours": ({
            "day": selected.get("day"),
            "enabled": bool(selected.get("enabled")),
            "open": selected.get("open"),
            "close": selected.get("close"),
        } if selected else None),
        "lead_time_minutes": 0,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "booking_horizon_days": 180,
    }


def _availability_summary(result: Dict[str, Any], timezone_name: str) -> Dict[str, Any]:
    slots = booking_slots_from_tool_result(result)
    starts = [_audit_iso(slot.get("start"), timezone_name) for slot in slots]
    ends = [_audit_iso(slot.get("end"), timezone_name) for slot in slots]
    starts = [item for item in starts if item]
    ends = [item for item in ends if item]
    return {
        "available": bool(slots),
        "slot_count": len(slots),
        "candidate_range": {
            "first_start": min(starts) if starts else None,
            "last_end": max(ends) if ends else None,
            "returned_count": len(slots),
            "bounded": True,
        },
        "conflict": {
            "classification": "none_observed" if slots else "occupied_or_policy_limited",
            "ids": [],
        },
    }


def _add_decision_event(
    db: Session,
    thread: Thread,
    audit: Dict[str, Any],
    *,
    result_code: str,
    interpreted_slot: Optional[str],
    pending_before: bool,
    generated_reply_message_id: Optional[str],
) -> None:
    _add_structured_thread_event(
        db, thread, "booking_decision", audit,
        interpreted_slot=_audit_iso(interpreted_slot, audit.get("timezone", "Australia/Hobart")),
        result_code=result_code,
        reason_code=result_code,
        pending_transition={
            "before": pending_before,
            "after": bool(thread.pending_booking),
            "accepted_slot": _audit_iso(interpreted_slot, audit.get("timezone", "Australia/Hobart"))
                if result_code in {"booking_created", "booking_already_confirmed"} else None,
        },
        generated_reply_message_id=generated_reply_message_id,
    )


def customer_booking_guidance(
    bookings: List[Dict[str, Any]],
    requested_time: Optional[datetime],
) -> tuple[str, bool]:
    """Render authoritative ownership context and flag an exact booking confirmation."""
    if not bookings:
        return "Customer booking context: no existing booking was found for this customer.", False
    lines = ["Customer booking context (authoritative; these bookings belong to this customer):"]
    requested_confirmed = False
    for booking in bookings:
        start = booking["start"]
        end = booking["end"]
        lines.append(
            f"- {start.strftime('%A %d %B at %I:%M %p')} to {end.strftime('%I:%M %p')}: "
            f"{booking.get('summary') or 'Appointment'}"
        )
        if requested_time and requested_time == start:
            requested_confirmed = True
    if requested_time:
        lines.append(f"Customer's explicit requested time: {requested_time.strftime('%I:%M %p')}.")
        if requested_confirmed:
            lines.append(
                "That exact time is already this customer's confirmed booking. Confirm it; "
                "never call it unavailable and never offer a replacement time."
            )
        elif any(booking["start"] < requested_time + timedelta(minutes=30) and booking["end"] > requested_time for booking in bookings):
            lines.append(
                "The requested time overlaps this customer's own booking. Do not describe it as "
                "another customer's conflict; clarify whether they want their existing booking moved."
            )
    return "\n".join(lines), requested_confirmed


def build_broad_availability_guidance(
    message: str,
    now_local: datetime,
    busy_slots: list[dict[str, datetime]],
    working_hours_by_day: dict[str, dict[str, Any]],
) -> str:
    """Summarise a broad time-of-day request without selecting arbitrary slots."""
    text = (message or "").lower()
    periods = (
        ("morning", 6, 12),
        ("afternoon", 12, 17),
        ("evening", 17, 24),
        ("tonight", 17, 24),
    )
    selected = next((period for period in periods if period[0] in text), None)
    if not selected:
        return ""

    label, start_hour, end_hour = selected
    target_date = (now_local + timedelta(days=1)).date() if "tomorrow" in text else now_local.date()
    period_start = datetime.combine(target_date, datetime.min.time(), tzinfo=now_local.tzinfo) + timedelta(hours=start_hour)
    period_end = datetime.combine(target_date, datetime.min.time(), tzinfo=now_local.tzinfo) + timedelta(hours=end_hour)
    earliest = now_local
    cursor = max(period_start, earliest)
    minutes = 15 * ((cursor.minute + 14) // 15)
    cursor = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)

    available_starts = 0
    while cursor < period_end:
        slot_end = cursor + timedelta(minutes=30)
        day_cfg = working_hours_by_day.get(DAY_NAMES[cursor.weekday()])
        if day_cfg and day_cfg.get("enabled", False):
            open_h, open_m = map(int, day_cfg["open"].split(":"))
            close_h, close_m = map(int, day_cfg["close"].split(":"))
            cursor_minutes = cursor.hour * 60 + cursor.minute
            end_minutes = slot_end.hour * 60 + slot_end.minute
            inside_hours = cursor_minutes >= open_h * 60 + open_m and end_minutes <= close_h * 60 + close_m
            overlaps = any(cursor < busy["end"] and slot_end > busy["start"] for busy in busy_slots)
            if inside_hours and not overlaps:
                available_starts += 1
        cursor += timedelta(minutes=15)

    day_label = "tomorrow" if target_date != now_local.date() else "today"
    if available_starts:
        return (
            f"Requested-period guidance: {label} {day_label} has availability. "
            "Confirm availability broadly and ask what time suits them. Do not list sample times."
        )
    return (
        f"Requested-period guidance: {label} {day_label} has no valid 30-minute opening. "
        "Do not claim availability in that period; respond briefly and offer a genuine alternative."
    )


def build_read_only_calendar_context(now: Optional[datetime] = None) -> str:
    """Build a compact availability snapshot without exposing calendar write actions."""
    tz_hobart = ZoneInfo("Australia/Hobart")
    if now is None:
        local_now = datetime.now(tz_hobart)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=tz_hobart)
    else:
        local_now = now.astimezone(tz_hobart)

    limit = local_now + timedelta(days=14)
    cs = _dyn("calendar_service", calendar_service)
    busy_slots = cs.get_busy_slots(local_now, limit)
    working_lines = []
    hours_loader = _dyn("load_working_hours", load_working_hours)
    for entry in hours_loader():
        if entry.get("enabled", False):
            working_lines.append(f"{entry['day']} {entry['open']}-{entry['close']}")
        else:
            working_lines.append(f"{entry['day']} closed")

    busy_lines = []
    for busy in busy_slots:
        start = busy["start"].astimezone(tz_hobart)
        end = busy["end"].astimezone(tz_hobart)
        busy_lines.append(
            f"- {start.strftime('%A %d %B %Y, %I:%M %p')} to {end.strftime('%I:%M %p')}"
        )
    if not busy_lines:
        busy_lines.append("- None")

    return (
        "READ-ONLY CALENDAR SNAPSHOT\n"
        f"Current local time (Australia/Hobart): {local_now.strftime('%A %d %B %Y, %I:%M %p %Z')}\n"
        f"Window ends: {limit.strftime('%A %d %B %Y, %I:%M %p %Z')}\n"
        f"Working hours: {'; '.join(working_lines)}\n"
        "Busy periods (never offer an overlapping time):\n"
        + "\n".join(busy_lines)
        + "\nAll other times inside working hours are available for enquiry. "
        "This snapshot may be used to answer availability, but Boot Camp cannot create, "
        "change, cancel, or confirm a booking."
    )


def is_booking_or_availability_turn(message: str) -> bool:
    """Identify turns that must use live calendar evidence only."""
    return bool(AVAILABILITY_REQUEST_RE.search(message or ""))


def has_availability_claim(reply: str) -> bool:
    """Return whether customer-facing wording makes an availability assertion."""
    return bool(AVAILABILITY_CLAIM_RE.search(reply or ""))


def validate_calendar_only_reply(reply: str, *, live_lookup_succeeded: bool) -> Optional[str]:
    """Reject availability statements that are not backed by this turn's calendar call."""
    if has_availability_claim(reply) and not live_lookup_succeeded:
        return "AI stated availability without a fresh live calendar lookup"
    return None


def requested_duration_minutes(messages: List[Any], current_body: str) -> Optional[int]:
    """Return the customer's most recently stated booking duration."""
    texts = [
        str(getattr(message, "text", ""))
        for message in messages
        if getattr(message, "role", None) == "customer"
    ]
    texts.append(current_body or "")
    for text in reversed(texts[-12:]):
        normalized = text.casefold()
        if re.search(r"\b(?:one|1)\s*(?:hour|hr)\b", normalized):
            return 60
        minute_match = re.search(r"\b(15|30|45|60|90)\s*(?:minute|minutes|min|mins)\b", normalized)
        if minute_match:
            return int(minute_match.group(1))
        if re.search(r"\bhalf\s*(?:an\s*)?(?:hour|hr)\b", normalized):
            return 30
    return None


def chronological_pending_booking_state(messages: List[Any], account_key: str) -> Optional[Dict[str, str]]:
    """Recover a literal, account-bound offered time while collecting a name."""
    if account_key not in FIRST_CONTACT_ACCOUNT_KEYS:
        return None
    meaningful = [
        (index, message) for index, message in enumerate(messages)
        if getattr(message, "role", "") in {"agent", "customer"}
    ]
    if not meaningful:
        return None
    customer_positions = [item for item in meaningful if getattr(item[1], "role", "") == "customer"]
    if customer_positions:
        customer_index, customer_message = customer_positions[-1]
        preceding = [item for item in meaningful if item[0] < customer_index]
        if not preceding:
            return None
        name_question_index, name_question = preceding[-1]
        if getattr(name_question, "role", "") != "agent":
            return None
        name_question_at = getattr(name_question, "at", None)
        customer_at = getattr(customer_message, "at", None)
        if isinstance(name_question_at, datetime) and isinstance(customer_at, datetime):
            if customer_at - name_question_at > timedelta(hours=2):
                return None
    else:
        name_question_index, name_question = meaningful[-1]
    text = str(getattr(name_question, "text", ""))
    if not re.search(r"\b(?:what|which)\s+(?:is\s+)?(?:your\s+)?name\b|\bname\s+(?:should|shall)\s+i\b", text, re.IGNORECASE):
        return None
    prior_to_question = [item for item in meaningful if item[0] < name_question_index]
    if not prior_to_question:
        return None
    offer_index, offer_message = prior_to_question[-1]
    if getattr(offer_message, "role", "") != "agent":
        return None
    offer_text = str(getattr(offer_message, "text", ""))
    match = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))\s*(am|pm)?\b", offer_text, re.IGNORECASE)
    has_date = bool(re.search(
        r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\b\d{4}-\d{2}-\d{2}\b",
        offer_text,
        re.IGNORECASE,
    ))
    if match and has_date:
        offer_at = getattr(offer_message, "at", None)
        name_question_at = getattr(name_question, "at", None)
        if isinstance(offer_at, datetime) and isinstance(name_question_at, datetime):
            if name_question_at - offer_at > timedelta(hours=2):
                return None
        clock = match.group(0).strip()
        return {"accepted_slot": clock, "state": "awaiting_customer_name", "account_key": account_key}
    return None


def name_only_follow_up_preserves_slot(
    messages: List[Any],
    account_key: str,
    customer_message: str,
    *,
    fresh_calendar_conflict: bool = False,
) -> Optional[Dict[str, str]]:
    """Resolve a name-only reply without silently replacing a prior offered time."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z '\-]{0,119}", (customer_message or "").strip()):
        return None
    state = chronological_pending_booking_state(messages, account_key)
    if not state:
        return None
    return {
        **state,
        "resolution": "fresh_calendar_conflict" if fresh_calendar_conflict else "preserve_pending_slot",
    }


def parse_customer_requested_slot(message: str, received_at: datetime) -> Optional[datetime]:
    """Parse a customer clock time against the inbound timestamp in Melbourne."""
    text = (message or "").casefold()
    match = re.search(r"(?<!\d)(1[0-2]|0?[1-9])(?:(?::|\.)([0-5]\d))?\s*(am|pm)?\b", text)
    if not match:
        return None
    received = received_at.replace(tzinfo=timezone.utc) if received_at.tzinfo is None else received_at
    local_received = received.astimezone(ZoneInfo(BOOKING_LOCAL_TIMEZONE))
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem:
        hour = (hour % 12) + (12 if meridiem == "pm" else 0)
    elif re.search(r"\b(?:afternoon|evening|tonight)\b", text):
        hour = (hour % 12) + 12
    else:
        hour %= 12
    requested_date = local_received.date() + timedelta(days=1 if re.search(r"\btomorrow\b", text) else 0)
    return datetime.combine(requested_date, datetime.min.time(), ZoneInfo(BOOKING_LOCAL_TIMEZONE)).replace(hour=hour, minute=minute)


def explicitly_requested_service(message: str, services: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", (message or "").casefold()).split())
    for service in services:
        service_id = str(service.get("id") or "").casefold()
        name = " ".join(re.sub(r"[^a-z0-9]+", " ", str(service.get("name") or "").casefold()).split())
        if (service_id and re.search(rf"(?<![a-z0-9]){re.escape(service_id)}(?![a-z0-9])", normalized)) or (name and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", normalized)):
            return service
    return None


def customer_slot_label(value: datetime) -> str:
    local = value.astimezone(ZoneInfo(BOOKING_LOCAL_TIMEZONE))
    return f"{local.strftime('%A')} at {local.strftime('%I:%M%p').lstrip('0').lower()}"


def exact_lookup_cache_key(
    account_key: str,
    service_id: str,
    start_time: str,
    calendar_binding: str = "business-calendar",
) -> Optional[tuple[str, str, str, int, str]]:
    """Return the decision-local identity for an exact live availability result."""
    service_getter = _dyn("get_service_for_booking", get_service_for_booking)
    service = service_getter(service_id, account_key)
    if not service:
        return None
    try:
        start = parse_business_datetime(start_time).replace(second=0, microsecond=0)
        duration = max(1, int(service.get("duration", 0)))
    except (TypeError, ValueError):
        return None
    return account_key, start.isoformat(), str(service["id"]), duration, calendar_binding


def validate_availability_claim(
    reply: str,
    tool_slots: List[Dict[str, Any]],
    requested_duration: Optional[int],
    now_local: datetime,
    live_calendar_lookup_succeeded: bool = False,
) -> Optional[str]:
    """Reject exact-time claims that disagree with exact-duration booking evidence."""
    normalized_reply = reply.casefold().replace("’", "'").replace("\ufffd", "'")
    if (
        re.search(r"\b(?:two|2)\b.*\b(?:30|thirty)\s*(?:minute|minutes|min|mins)\b", normalized_reply)
        or "back-to-back" in normalized_reply
        or "back to back" in normalized_reply
    ) and re.search(r"\b(?:i\s+can|can\s+do|book|available)\b", normalized_reply):
        return "AI attempted to combine separate short appointments into a longer service"

    extractor = _dyn("requested_time_at_receipt", None)
    if extractor is None:
        try:
            from backend.services.sms_service import requested_time_at_receipt as extractor
        except ImportError:
            from services.sms_service import requested_time_at_receipt as extractor

    claimed_time = extractor(reply, now_local) if callable(extractor) else None
    if not claimed_time:
        return None
    negative = bool(re.search(
        r"\b(can\W+t|cannot|can not|not available|not free|isn\W+t available|is not available|don\W+t have|do not have)\b",
        normalized_reply,
    ))
    if not negative and not re.search(
        r"\b(available|availability|free|spot|opening|can\s*(?:not|'t)?\s*do|can't\s*do|cannot\s*do)\b",
        normalized_reply,
    ):
        return None

    matching_slots = []
    for slot in tool_slots:
        try:
            start = parse_business_datetime(slot["start"])
            end = parse_business_datetime(slot["end"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = int((end - start).total_seconds() // 60)
        if requested_duration is not None and duration != requested_duration:
            continue
        if start.replace(second=0, microsecond=0) == claimed_time.replace(second=0, microsecond=0):
            matching_slots.append(slot)

    if negative and matching_slots:
        return "AI said a provider-validated exact-duration slot was unavailable"
    if negative and not matching_slots and not live_calendar_lookup_succeeded:
        return "AI stated an exact time was unavailable without matching live calendar evidence"
    if not negative and not matching_slots:
        return "AI claimed an exact time without matching exact-duration provider evidence"
    return None


def booking_slots_from_tool_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract only provider-validated, service-specific slots from a discovery result."""
    candidates = result.get("slots")
    if candidates is None and result.get("next_available"):
        candidates = [result["next_available"]]
    if candidates is None and result.get("exact_slot"):
        candidates = [result["exact_slot"]]
    if not isinstance(candidates, list):
        return []
    return [
        {
            "service_id": str(slot.get("service_id", result.get("service_id", ""))),
            "start": str(slot.get("start_time", "")),
            "end": str(slot.get("end_time", "")),
        }
        for slot in candidates
        if isinstance(slot, dict) and slot.get("start_time") and slot.get("end_time")
    ]


def booking_proposal_has_live_evidence(
    service_id: str,
    start_time: str,
    verified_slots: List[Dict[str, Any]],
) -> bool:
    """Require an exact provider-validated service/time before saving a proposal."""
    try:
        proposed_start = parse_business_datetime(start_time).replace(second=0, microsecond=0)
    except (TypeError, ValueError):
        return False
    for slot in verified_slots:
        if str(slot.get("service_id") or "") != str(service_id or ""):
            continue
        try:
            verified_start = parse_business_datetime(str(slot["start"])).replace(
                second=0,
                microsecond=0,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if verified_start == proposed_start:
            return True
    return False


def get_booking_tool_suite(account_key: str) -> Any:
    """Build discovery tools bound to the resolved provider, never all lines."""
    resolve_provider_context(account_key)
    try:
        from backend.config.timezone import get_tenant_timezone
        from backend.booking_tools import (
            BookingToolSuite,
            FastAPIBookingsDiscoveryProvider,
            LegacyCalendarDiscoveryProvider,
        )
    except ImportError:
        from config.timezone import get_tenant_timezone
        from booking_tools import (
            BookingToolSuite,
            FastAPIBookingsDiscoveryProvider,
            LegacyCalendarDiscoveryProvider,
        )

    tenant_tz = get_tenant_timezone(account_key, required_for_scheduling=True)
    timezone_name = getattr(tenant_tz, "key", str(tenant_tz)) or str(tenant_tz)
    backend_name = os.getenv("BOOKING_BACKEND", "legacy").strip().casefold()
    if backend_name == "fastapi":
        provider = FastAPIBookingsDiscoveryProvider(
            base_url=os.getenv("FASTAPI_BOOKINGS_URL", ""),
            tenant=os.getenv("FASTAPI_BOOKINGS_TENANT"),
            token=os.getenv("FASTAPI_BOOKINGS_TOKEN"),
            provider_id=os.getenv(f"FASTAPI_BOOKINGS_PROVIDER_{account_key.upper()}_ID"),
        )
    else:
        cs = _dyn("calendar_service", calendar_service)
        def busy_slots_loader(start: datetime, end: datetime) -> List[Dict[str, datetime]]:
            return cs.get_busy_slots_for_account(
                start, end, account_key, require_authoritative=True,
            )
        line_loader = _dyn("load_line_services", None)
        hours_loader = _dyn("load_working_hours", load_working_hours)
        provider = LegacyCalendarDiscoveryProvider(
            services_loader=lambda: line_loader(account_key) if callable(line_loader) else [],
            working_hours_loader=hours_loader,
            busy_slots_loader=busy_slots_loader,
            timezone_name=timezone_name,
        )
    return BookingToolSuite(provider, timezone_name, account_key=account_key)


def load_booking_reminder_config() -> Dict[str, Any]:
    default = {"enabled": True, "minutesBefore": 60, "template": DEFAULT_BOOKING_REMINDER_TEMPLATE}
    config_path = _dyn("BOOKING_REMINDER_CONFIG_PATH", BOOKING_REMINDER_CONFIG_PATH)
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            return {
                "enabled": bool(saved.get("enabled", True)),
                "minutesBefore": min(10080, max(5, int(saved.get("minutesBefore", 60)))),
                "template": str(saved.get("template") or DEFAULT_BOOKING_REMINDER_TEMPLATE),
            }
    except Exception as exc:
        logger.warning("Could not load booking reminder settings: %s", type(exc).__name__)
    return default


def _booking_reminder_parts(
    summary: str,
    sms_account_key: Optional[str] = None,
) -> tuple[str, str, str, str]:
    if sms_account_key in FIRST_CONTACT_ACCOUNT_KEYS:
        provider_key = "anonymous" if sms_account_key == "secondary" else "tori"
    else:
        provider_key = "anonymous" if re.search(r"\(Anonymous\)\s*$", summary or "", re.IGNORECASE) else "tori"
    provider = BOOKING_PROVIDERS[provider_key]
    cleaned = re.sub(r"\s*\((?:Tori|Anonymous)\)\s*$", "", summary or "", flags=re.IGNORECASE)
    if " - " in cleaned:
        name, service = cleaned.split(" - ", 1)
    else:
        name, service = "there", cleaned or "your appointment"
    return name.strip(), service.strip(), provider["name"], provider["sms_account_key"]


def _read_sent_booking_reminders() -> set[str]:
    sent_path = _dyn("BOOKING_REMINDER_SENT_PATH", BOOKING_REMINDER_SENT_PATH)
    try:
        if os.path.exists(sent_path):
            with open(sent_path, "r", encoding="utf-8") as handle:
                return set(json.load(handle))
    except Exception:
        pass
    return set()


def _write_sent_booking_reminders(sent: set[str]) -> None:
    sent_path = _dyn("BOOKING_REMINDER_SENT_PATH", BOOKING_REMINDER_SENT_PATH)
    os.makedirs(os.path.dirname(sent_path), exist_ok=True)
    temporary_path = sent_path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(sorted(sent), handle, indent=2)
    os.replace(temporary_path, sent_path)


def process_due_booking_reminders() -> None:
    config_loader = _dyn("load_booking_reminder_config", load_booking_reminder_config)
    config = config_loader()
    if not config["enabled"]:
        return
    now = datetime.now()
    due_limit = now + timedelta(minutes=config["minutesBefore"])
    session_factory = _dyn("SessionLocal", SessionLocal)
    db = session_factory()
    reminder_lock = _dyn("BOOKING_REMINDER_LOCK", BOOKING_REMINDER_LOCK)
    try:
        due_bookings = db.query(CalendarEvent).filter(
            CalendarEvent.status == "scheduled",
            CalendarEvent.start_time > now,
            CalendarEvent.start_time <= due_limit,
            CalendarEvent.customer_phone.isnot(None),
        ).order_by(CalendarEvent.start_time.asc()).all()
        with reminder_lock:
            sent = _read_sent_booking_reminders()
            for booking in due_bookings:
                if booking.id in sent:
                    continue
                name, service, provider_name, account_key = _booking_reminder_parts(
                    booking.summary,
                    booking.sms_account_key,
                )
                formatted_time = booking.start_time.strftime("%A, %b %d at %I:%M %p")

                get_vars_fn = _dyn("get_business_variable_values", None)
                biz_vars = get_vars_fn() if callable(get_vars_fn) else {}

                variables = {
                    **biz_vars,
                    "name": name,
                    "service": service,
                    "provider": provider_name,
                    "provider_name": provider_name,
                    "time": formatted_time,
                    "date": booking.start_time.strftime("%A, %b %d"),
                    "customer_phone": booking.customer_phone or "",
                }
                render_fn = _dyn("render_template_variables", None)
                if render_fn is None:
                    try:
                        from backend.knowledge import render_template_variables as render_fn
                    except ImportError:
                        from knowledge import render_template_variables as render_fn

                sms_text = render_fn(config["template"], variables)
                reminder_key = f"booking-reminder:{booking.id}"
                result = mobilemessage_service.send_sms(
                    booking.customer_phone,
                    sms_text,
                    idempotency_key=reminder_key,
                    account_key=account_key,
                )
                failure = mobilemessage_service.delivery_error(result)
                if failure:
                    logger.warning("Booking reminder delivery failed for booking %s", booking.id)
                    continue

                thread_finder = _dyn("find_thread_by_phone", None)
                if thread_finder is None:
                    try:
                        from backend.services.sms_service import find_thread_by_phone as thread_finder
                    except ImportError:
                        from services.sms_service import find_thread_by_phone as thread_finder

                thread = thread_finder(db, booking.customer_phone, account_key) if callable(thread_finder) else None
                if thread:
                    db.add(Message(
                        id=str(uuid.uuid4()),
                        thread_id=thread.id,
                        role="agent",
                        text=sms_text,
                        provider_message_id=reminder_key,
                        at=datetime.utcnow(),
                    ))
                    thread.updated_at = datetime.utcnow()
                sent.add(booking.id)
                _write_sent_booking_reminders(sent)
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Booking reminder check failed: %s", type(exc).__name__)
    finally:
        db.close()


async def booking_reminder_worker() -> None:
    while True:
        await asyncio.to_thread(process_due_booking_reminders)
        await asyncio.sleep(30)


__all__ = [
    "booking_availability_error",
    "propose_conversational_booking",
    "confirm_conversational_booking",
    "get_service_for_booking",
    "is_explicit_booking_confirmation",
    "is_explicit_booking_rejection",
    "asks_for_secondary_booking_confirmation",
    "parse_business_datetime",
    "current_business_time",
    "_availability_policy_inputs",
    "_availability_summary",
    "_add_decision_event",
    "customer_booking_guidance",
    "build_broad_availability_guidance",
    "build_read_only_calendar_context",
    "validate_availability_claim",
    "validate_calendar_only_reply",
    "has_availability_claim",
    "is_booking_or_availability_turn",
    "name_only_follow_up_preserves_slot",
    "booking_slots_from_tool_result",
    "booking_proposal_has_live_evidence",
    "chronological_pending_booking_state",
    "load_booking_services",
    "load_booking_reminder_config",
    "_read_sent_booking_reminders",
    "_write_sent_booking_reminders",
    "process_due_booking_reminders",
    "booking_reminder_worker",
    "to_naive_utc",
    "_audit_iso",
    "_add_structured_thread_event",
    "_booking_reminder_parts",
    "BOOKING_PROVIDERS",
    "load_working_hours",
    "requested_duration_minutes",
    "get_booking_tool_suite",
    "parse_customer_requested_slot",
    "explicitly_requested_service",
    "customer_slot_label",
    "exact_lookup_cache_key",
]

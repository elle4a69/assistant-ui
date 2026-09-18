"""Booking, calendar, and availability routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import os
from typing import Any, Dict, List, Optional
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

try:
    from backend.core.config import BOOKING_REMINDER_CONFIG_PATH, PROMPTS_DIR
    from backend.core.constants import DAY_NAMES
    from backend.core.database import get_db
    from backend.core.clients import calendar_service, mobilemessage_service
    from backend.core.utils import format_dt, _dyn
    from backend.models.domain import CalendarEvent, Thread, Message, ThreadEvent
    from backend.schemas.domain import UpdateBookingInput, BookingReminderInput, ManualBookingInput
    from backend.services.booking_service import (
        BOOKING_PROVIDERS,
        load_working_hours,
        _booking_reminder_parts,
        parse_business_datetime,
        booking_availability_error,
        get_service_for_booking,
        load_booking_services,
        load_booking_reminder_config,
        queue_booking_notification as _queue_booking_notification,
        booking_notification_snapshot as _booking_notification_snapshot,
    )
    from backend.services.settings_service import (
        get_business_variable_values, load_business_variables, load_line_services,
    )
    from backend.services.sms_service import find_thread_by_phone
    from backend.services.arrival_service import (
        _issue_arrival_invite, _arrival_booking, _arrival_public_link,
    )
    from backend.knowledge import render_template_variables
except ImportError:
    from core.config import BOOKING_REMINDER_CONFIG_PATH, PROMPTS_DIR
    from core.constants import DAY_NAMES
    from core.database import get_db
    from core.clients import calendar_service, mobilemessage_service
    from core.utils import format_dt, _dyn
    from models.domain import CalendarEvent, Thread, Message, ThreadEvent
    from schemas.domain import UpdateBookingInput, BookingReminderInput, ManualBookingInput
    from services.booking_service import (
        BOOKING_PROVIDERS,
        load_working_hours,
        _booking_reminder_parts,
        parse_business_datetime,
        booking_availability_error,
        get_service_for_booking,
        load_booking_services,
        load_booking_reminder_config,
        queue_booking_notification as _queue_booking_notification,
        booking_notification_snapshot as _booking_notification_snapshot,
    )
    from services.settings_service import (
        get_business_variable_values, load_business_variables, load_line_services,
    )
    from services.sms_service import find_thread_by_phone
    from services.arrival_service import (
        _issue_arrival_invite, _arrival_booking, _arrival_public_link,
    )
    from knowledge import render_template_variables

router = APIRouter()

@router.get("/api/calendar/bookings")
def get_bookings(
    db: Session = Depends(get_db),
    include_past: bool = Query(False, alias="includePast"),
):
    from zoneinfo import ZoneInfo
    tz_hobart = ZoneInfo("Australia/Hobart")
    now_utc = datetime.now(timezone.utc)
    now_hobart = now_utc.astimezone(tz_hobart).replace(tzinfo=None)

    def format_booking_dt(dt: datetime) -> str:
        """Return an ISO timestamp with the real Hobart UTC offset."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz_hobart)
        return dt.astimezone(tz_hobart).isoformat()

    def financial_details(
        summary: str,
        sms_account_key: Optional[str],
        saved_amount: Optional[int] = None,
    ) -> tuple[str, Optional[int]]:
        _, service_name, provider_name, account_key = _booking_reminder_parts(
            summary,
            sms_account_key,
        )
        if saved_amount is not None:
            return provider_name, saved_amount
        normalized_service_name = " ".join(service_name.casefold().split())
        for provider_suffix in (" (anonymous)", " (tori)"):
            if normalized_service_name.endswith(provider_suffix):
                normalized_service_name = normalized_service_name[:-len(provider_suffix)].strip()
                break
        verified_legacy_prices = {
            "my friend is offerring this anonymously": 200,
            "deepthroat bbbj cim": 200,
            "full service": 250,
            "girlfriend experience (gfe)": 300,
        }
        if normalized_service_name in verified_legacy_prices:
            return provider_name, verified_legacy_prices[normalized_service_name]
        # Compatibility for bookings created before price snapshots existed.
        for service in load_line_services(account_key):
            if str(service.get("name") or "").strip().casefold() == service_name.casefold():
                try:
                    return provider_name, int(service.get("price"))
                except (TypeError, ValueError):
                    break
        return provider_name, None

    results = []
    
    if calendar_service.service:
        try:
            calendar_id = os.getenv("CALENDAR_ID", "primary")
            list_arguments: Dict[str, Any] = {
                "calendarId": calendar_id,
                "orderBy": "startTime",
                "singleEvents": True,
            }
            if not include_past:
                # The default feed drives the live booking alert poller. Do not
                # send historical events to old or current PWA clients.
                list_arguments["timeMin"] = now_utc.isoformat().replace("+00:00", "Z")
            events_result = calendar_service.service.events().list(**list_arguments).execute()
            events = events_result.get('items', [])
            for e in events:
                start_raw = e["start"].get("dateTime", e["start"].get("date"))
                end_raw = e["end"].get("dateTime", e["end"].get("date"))
                
                # Parse as timezone-aware datetime
                b_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                
                if b_end.tzinfo is None:
                    b_end = b_end.replace(tzinfo=tz_hobart)
                if not include_past and b_end.astimezone(timezone.utc) <= now_utc:
                    continue

                # Convert to Hobart local time; the response formatter restores the explicit offset.
                b_start_local = b_start.astimezone(tz_hobart).replace(tzinfo=None)
                b_end_local = b_end.astimezone(tz_hobart).replace(tzinfo=None)
                
                desc = e.get("description", "")
                customer_phone = desc.replace("Customer phone: ", "") if "Customer phone: " in desc else None
                provider_name, amount = financial_details(e.get("summary") or "", None)
                results.append({
                    "id": e.get("id"),
                    "customerPhone": customer_phone,
                    "summary": e.get("summary"),
                    "smsAccountKey": None,
                    "threadId": None,
                    "startTime": format_booking_dt(b_start_local),
                    "endTime": format_booking_dt(b_end_local),
                    "status": "scheduled",
                    "notes": desc,
                    "providerName": provider_name,
                    "amount": amount,
                })
        except Exception as ex:
            print(f"Error listing Google Calendar events: {ex}")
            
    db_events_query = db.query(CalendarEvent)
    if not include_past:
        db_events_query = db_events_query.filter(CalendarEvent.end_time > now_hobart)
    db_events = db_events_query.order_by(CalendarEvent.start_time.asc()).all()
    for de in db_events:
        # de.start_time and de.end_time are naive local Hobart times in database.
        # Return them with an explicit Hobart offset so browsers preserve the booked time.
        de_start_str = format_booking_dt(de.start_time)
        existing_result = next((
            result for result in results
            if result["id"] == de.id
            or (result["startTime"] == de_start_str and result["customerPhone"] == de.customer_phone)
        ), None)
        if existing_result:
            existing_result["smsAccountKey"] = de.sms_account_key
            existing_result["threadId"] = de.thread_id
            existing_result["status"] = getattr(de, "status", "scheduled") or "scheduled"
            existing_result["notes"] = getattr(de, "notes", "") or existing_result.get("notes", "")
            provider_name, amount = financial_details(de.summary, de.sms_account_key, de.amount)
            existing_result["providerName"] = provider_name
            existing_result["amount"] = amount
        else:
            provider_name, amount = financial_details(de.summary, de.sms_account_key, de.amount)
            results.append({
                "id": de.id,
                "customerPhone": de.customer_phone,
                "summary": de.summary,
                "smsAccountKey": de.sms_account_key,
                "threadId": de.thread_id,
                "startTime": de_start_str,
                "endTime": format_booking_dt(de.end_time),
                "status": getattr(de, "status", "scheduled") or "scheduled",
                "notes": getattr(de, "notes", "") or "",
                "providerName": provider_name,
                "amount": amount,
            })
            
    return results


@router.put("/api/calendar/bookings/{booking_id}")
def update_booking_endpoint(
    booking_id: str,
    payload: UpdateBookingInput,
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    from zoneinfo import ZoneInfo
    tz_hobart = ZoneInfo("Australia/Hobart")

    def format_booking_dt(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz_hobart)
        return dt.astimezone(tz_hobart).isoformat()

    booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()
    existed = booking is not None
    before = (
        booking.summary, booking.customer_phone, booking.status,
        booking.start_time, booking.end_time,
    ) if booking else None
    
    if not booking:
        dt_now = datetime.utcnow()
        booking = CalendarEvent(
            id=booking_id,
            summary=payload.summary or "Scheduled Appointment",
            customer_phone=payload.customerPhone,
            start_time=dt_now,
            end_time=dt_now + timedelta(minutes=30),
            status=payload.status or "scheduled",
            notes=payload.notes or ""
        )
        db.add(booking)

    if payload.summary is not None:
        booking.summary = payload.summary
    if payload.customerPhone is not None:
        booking.customer_phone = payload.customerPhone
    if payload.status is not None:
        booking.status = payload.status
    if payload.notes is not None:
        booking.notes = payload.notes
    if payload.amount is not None:
        if payload.amount < 0:
            raise HTTPException(status_code=422, detail="Booking amount must be zero or greater")
        booking.amount = payload.amount
        
    if payload.startTime is not None:
        try:
            dt_start = parse_business_datetime(payload.startTime)
            booking.start_time = dt_start.replace(tzinfo=None)
        except Exception:
            pass
            
    if payload.endTime is not None:
        try:
            dt_end = parse_business_datetime(payload.endTime)
            booking.end_time = dt_end.replace(tzinfo=None)
        except Exception:
            pass

    db.commit()
    db.refresh(booking)

    if existed:
        after = (booking.summary, booking.customer_phone, booking.status, booking.start_time, booking.end_time)
        if before != after:
            cancelled = str(booking.status or "").casefold() in {"cancelled", "canceled"}
            _queue_booking_notification(
                background_tasks,
                booking,
                notification_type="booking_cancelled" if cancelled else "booking_updated",
                title="Booking Cancelled" if cancelled else "Booking Updated",
                priority=3,
            )

    if calendar_service.service:
        try:
            calendar_id = os.getenv("CALENDAR_ID", "primary")
            body = {}
            if payload.summary is not None:
                body["summary"] = payload.summary
            if payload.customerPhone is not None:
                body["description"] = f"Customer phone: {payload.customerPhone}"
            if payload.startTime is not None:
                body["start"] = {"dateTime": format_booking_dt(booking.start_time)}
            if payload.endTime is not None:
                body["end"] = {"dateTime": format_booking_dt(booking.end_time)}
            if body:
                calendar_service.service.events().patch(
                    calendarId=calendar_id, eventId=booking_id, body=body
                ).execute()
        except Exception as e:
            print(f"Google Calendar patch failed/skipped: {e}")

    return {
        "id": booking.id,
        "customerPhone": booking.customer_phone,
        "summary": booking.summary,
        "smsAccountKey": booking.sms_account_key,
        "threadId": booking.thread_id,
        "startTime": format_booking_dt(booking.start_time),
        "endTime": format_booking_dt(booking.end_time),
        "status": getattr(booking, "status", "scheduled") or "scheduled",
        "notes": getattr(booking, "notes", "") or "",
        "providerName": _booking_reminder_parts(booking.summary, booking.sms_account_key)[2],
        "amount": booking.amount,
    }


@router.delete("/api/calendar/bookings/{booking_id}")
def delete_booking_endpoint(
    booking_id: str,
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    current_booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()
    booking_snapshot = _booking_notification_snapshot(current_booking) if current_booking else None
    success = calendar_service.delete_booking(booking_id)
    if not success:
        booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()
        if booking:
            db.delete(booking)
            db.commit()
            _queue_booking_notification(
                background_tasks, booking_snapshot, notification_type="booking_cancelled",
                title="Booking Cancelled", priority=3,
            )
            return {"status": "success"}
        raise HTTPException(status_code=404, detail="Booking not found or could not be deleted.")
    if booking_snapshot:
        _queue_booking_notification(
            background_tasks, booking_snapshot, notification_type="booking_cancelled",
            title="Booking Cancelled", priority=3,
        )
    return {"status": "success"}


@router.get("/api/calendar/freebusy")
def get_free_slots_endpoint(duration: int = Query(30), db: Session = Depends(get_db)):
    working_hours = load_working_hours()
    wh_by_day = {entry["day"]: entry for entry in working_hours}

    from zoneinfo import ZoneInfo
    tz_hobart = ZoneInfo("Australia/Hobart")

    now = datetime.now(tz_hobart)
    dt = now

    minutes = 15 * ((dt.minute + 14) // 15)
    dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)

    busy_slots = calendar_service.get_busy_slots(dt, dt + timedelta(days=14))
    free_slots = []
    limit_dt = dt + timedelta(days=14)

    while dt < limit_dt and len(free_slots) < 500:
        day_name = DAY_NAMES[dt.weekday()]
        day_cfg = wh_by_day.get(day_name)

        if day_cfg and day_cfg.get("enabled", False):
            open_h, open_m = map(int, day_cfg["open"].split(":"))
            close_h, close_m = map(int, day_cfg["close"].split(":"))
            open_mins = open_h * 60 + open_m
            close_mins = close_h * 60 + close_m
            dt_mins = dt.hour * 60 + dt.minute

            slot_end = dt + timedelta(minutes=duration)
            slot_end_mins = slot_end.hour * 60 + slot_end.minute

            if dt_mins >= open_mins and slot_end_mins <= close_mins:
                overlap = False
                for busy in busy_slots:
                    if dt < busy["end"] and slot_end > busy["start"]:
                        overlap = True
                        break
                if not overlap:
                    free_slots.append({
                        "startTime": format_dt(dt),
                        "endTime": format_dt(slot_end),
                    })
        dt += timedelta(minutes=15)

    return free_slots


@router.get("/api/settings/booking-reminder")
def get_booking_reminder_settings():
    return load_booking_reminder_config()


@router.post("/api/settings/booking-reminder")
def save_booking_reminder_settings(payload: BookingReminderInput):
    os.makedirs(os.path.dirname(BOOKING_REMINDER_CONFIG_PATH), exist_ok=True)
    with open(BOOKING_REMINDER_CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload.model_dump(), handle, indent=2)
    return {"status": "success"}


@router.post("/api/calendar/bookings")
def create_manual_booking(
    payload: ManualBookingInput,
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    mm_service = _dyn("mobilemessage_service", mobilemessage_service)
    normalized_destination = mm_service.normalize_sms_destination(payload.phone)
    if not normalized_destination:
        raise HTTPException(
            status_code=422,
            detail=(
                "Enter a valid Australian mobile number in 04xx xxx xxx "
                "or +614xx xxx xxx format."
            ),
        )

    try:
        customer_phone = "+" + normalized_destination
        provider = BOOKING_PROVIDERS[payload.providerKey]
        sms_account_key = provider["sms_account_key"]
        start_dt = parse_business_datetime(payload.startTime)
        
        services = load_line_services(sms_account_key)
                
        service = None
        for s in services:
            if s.get("id") == payload.serviceId:
                service = s
                break
                
        if not service:
            service = {
                "name": "Custom Appointment",
                "duration": 60,
                "price": 100
            }
            
        duration = service.get("duration", 60)
        end_dt = start_dt + timedelta(minutes=duration)
        avail_err_fn = _dyn("booking_availability_error", booking_availability_error)
        availability_error = avail_err_fn(start_dt, duration, sms_account_key)
        if availability_error:
            raise HTTPException(status_code=409, detail=availability_error)

        summary = f"{payload.name} - {service['name']} ({provider['name']})"
        cal_service = _dyn("calendar_service", calendar_service)
        booking_id = cal_service.create_booking(
            summary=summary,
            start=start_dt,
            end=end_dt,
            customer_phone=customer_phone,
            sms_account_key=sms_account_key,
        )
        if not booking_id:
            raise HTTPException(status_code=500, detail="Failed to create booking in calendar service.")

        arrival_session, arrival_token = _issue_arrival_invite(
            db,
            booking_id=str(booking_id),
            summary=summary,
            customer_phone=customer_phone,
            sms_account_key=sms_account_key,
            start_time=start_dt,
            end_time=end_dt,
        )
        local_booking = _arrival_booking(db, str(booking_id))
        if local_booking:
            local_booking.amount = int(service.get("price", 0) or 0)
        arrival_link = _arrival_public_link(arrival_token)
            
        template_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt")
        template = (
            "Hi {name}, your booking for {service} on {time} is confirmed!\n\n"
            "When you arrive, tap: {arrival_link}"
        )
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
                
        formatted_time = start_dt.strftime("%A, %b %d at %I:%M %p")
        confirmation_variables = {
            **get_business_variable_values(),
            "name": payload.name,
            "service": service["name"],
            "provider": provider["name"],
            "time": formatted_time,
            "arrival_link": arrival_link,
        }
        sms_text = render_template_variables(template, confirmation_variables)
        if "{arrival_link}" not in template:
            sms_text = f"{sms_text.rstrip()}\n\nWhen you arrive, tap: {arrival_link}"
        
        # Load website-only display confirmation screen template
        screen_template_path = os.path.join(PROMPTS_DIR, "website_confirmation_template.txt")
        screen_template = (
            "Hi {name}, your booking for {service} on {time} is confirmed!\n\n"
            "You will receive an SMS from me shortly with the address details.\n\n"
            "If you do not receive it in the next 20 minutes, please send me a message. See you then! - {provider}"
        )
        if os.path.exists(screen_template_path):
            try:
                with open(screen_template_path, "r", encoding="utf-8") as f:
                    screen_template = f.read()
            except Exception:
                pass
        screen_text = render_template_variables(screen_template, confirmation_variables)

        thread = find_thread_by_phone(db, customer_phone, sms_account_key)
        if not thread:
            thread = Thread(
                id=str(uuid.uuid4()),
                customer_phone=customer_phone,
                sms_account_key=sms_account_key,
                state="resolved",
                priority="medium",
                sla_due_at=start_dt + timedelta(hours=24),
                unread_count=0,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(thread)
            db.flush()
        else:
            thread.state = "resolved"
            
        confirmation_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="agent",
            text=sms_text,
            at=datetime.utcnow()
        )
        dispatch_result = mobilemessage_service.send_sms(
            customer_phone,
            sms_text,
            idempotency_key=confirmation_msg.id,
            account_key=sms_account_key,
        )
        delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
        if dispatch_result.get("status") == "skipped" or (delivery_failure and ("skipped" in str(delivery_failure).lower() or "not configured" in str(delivery_failure).lower())):
            delivery_failure = None
        if delivery_failure:
            confirmation_msg.role = "draft"
            thread.state = "needs-review"
        db.add(confirmation_msg)
        
        event = ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="sms-delivery-failed" if delivery_failure else "resolution",
            agent_id="system",
            meta=json.dumps({
                "detail": f"Booked {service['name']} for {payload.name}",
                **({"reason": delivery_failure[:500]} if delivery_failure else {}),
            }),
            at=datetime.utcnow()
        )
        db.add(event)
        db.commit()
        # The calendar, arrival link, conversation, and confirmation record are
        # durable before this best-effort owner notification is scheduled.
        saved_booking = _arrival_booking(db, str(booking_id))
        if saved_booking:
            _queue_booking_notification(
                background_tasks, saved_booking, notification_type="new_booking",
                title="New Booking", priority=4,
            )
        
        return {
            "status": "partial" if delivery_failure else "success",
            "smsSent": "" if delivery_failure else screen_text,
            "smsError": "Booking saved, but the confirmation SMS was not sent." if delivery_failure else None,
            "arrivalLink": arrival_link,
            "arrivalSessionId": arrival_session.id,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Booking creation failed: {e}")


__all__ = [
    "router",
    "get_bookings",
    "update_booking_endpoint",
    "delete_booking_endpoint",
    "get_free_slots_endpoint",
    "get_booking_reminder_settings",
    "save_booking_reminder_settings",
    "create_manual_booking",
]

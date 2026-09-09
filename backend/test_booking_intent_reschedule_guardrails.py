import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main


TZ = ZoneInfo("Australia/Melbourne")
RECEIVED = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    main.Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_thread(db, thread_id="thread", account="secondary"):
    now = datetime(2026, 9, 9, 10, 0)
    thread = main.Thread(
        id=thread_id,
        customer_phone="+61400000000",
        sms_account_key=account,
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.commit()
    return thread


def test_constraint_times_are_not_booking_requests_but_move_target_is():
    blocked = (
        "I have work at 11am tomorrow so I need to cancel",
        "I can't make 10:30 tomorrow",
        "I'm not available at 9:15pm",
        "No service, cancel it please",
    )
    for text in blocked:
        assert main.parse_customer_requested_slot(text, RECEIVED) is None

    text = "Can you move it from 9:15pm to 9:30pm so I'm not late?"
    assert main.is_explicit_reschedule_request(text)
    assert main.parse_reschedule_target_slot(text, RECEIVED).strftime("%H:%M") == "21:30"
    assert main.parse_customer_requested_slot("Tomorrow at 10am please", RECEIVED).strftime("%H:%M") == "10:00"

    time_only = main.parse_reschedule_target_slot("Can you make it 9:30?", RECEIVED)
    aligned = main.align_reschedule_target_with_existing_booking(
        "Can you make it 9:30?", time_only, datetime(2026, 9, 10, 21, 15, tzinfo=TZ),
    )
    assert aligned == datetime(2026, 9, 10, 21, 30, tzinfo=TZ)
    assert main.is_explicit_booking_cancellation_request("Please cancel my appointment")
    assert not main.is_explicit_booking_cancellation_request("I have work at 11am")


def test_pending_service_time_is_only_reused_for_a_direct_service_answer():
    services = [{"id": "service", "name": "Relaxation Session"}]
    assert main.is_pending_service_answer("Relaxation Session", services)
    assert main.is_pending_service_answer("2", services)
    for text in ("Sorry", "Why do you keep sending that?", "No service", "Cancel", "Please"):
        assert not main.is_pending_service_answer(text, services)


def test_identical_reply_is_suppressed_only_for_unchanged_booking_state():
    db = make_db()
    thread = add_thread(db)
    reply = "What service would you like for Thursday at 11:00am?"
    thread.pending_booking = json.dumps({
        "state": "awaiting_service",
        "requested_slot": "2026-09-10T11:00:00+10:00",
    })
    sent = main.Message(
        id="sent", thread_id=thread.id, role="system", text=reply,
        at=datetime(2026, 9, 9, 10, 1),
    )
    db.add(sent)
    db.add(main.ThreadEvent(
        id="sent-event", thread_id=thread.id, type="auto-reply-sent",
        agent_id=None,
        meta=json.dumps({
            "decision_state_fingerprint": main.automated_reply_state_fingerprint(thread, reply),
        }),
        at=datetime(2026, 9, 9, 10, 1),
    ))
    db.commit()

    assert main.identical_ai_reply_exists_for_unchanged_state(db, thread, reply)
    thread.pending_booking = None
    assert not main.identical_ai_reply_exists_for_unchanged_state(db, thread, reply)
    db.close()


def test_reschedule_excludes_only_the_target_shared_calendar_event(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    target = main.CalendarEvent(
        id="target-event",
        summary="Customer - Service (Anonymous)",
        customer_phone=thread.customer_phone,
        sms_account_key=thread.sms_account_key,
        thread_id=thread.id,
        start_time=datetime(2026, 9, 10, 21, 15),
        end_time=datetime(2026, 9, 10, 22, 15),
        status="scheduled",
    )
    db.add(target)
    db.commit()

    service = main.GoogleCalendarService(lambda: db)
    monkeypatch.setattr(main, "calendar_service", service)
    monkeypatch.setattr(main, "resolve_provider_context", lambda account: {"provider_name": account})
    monkeypatch.setattr(main, "current_business_time", lambda: datetime(2026, 9, 9, 12, 0, tzinfo=TZ))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": "Thursday", "enabled": True, "open": "00:00", "close": "23:59"},
    ])
    requested = datetime(2026, 9, 10, 21, 30, tzinfo=TZ)

    assert "overlaps" in main.booking_availability_error(requested, 60, "secondary")
    assert main.booking_availability_error(
        requested, 60, "secondary", exclude_event_id=target.id,
    ) is None

    other = main.CalendarEvent(
        id="other-provider-event",
        summary="Other booking",
        customer_phone="+61499999999",
        sms_account_key="primary",
        thread_id="other-thread",
        start_time=datetime(2026, 9, 10, 21, 45),
        end_time=datetime(2026, 9, 10, 22, 0),
        status="scheduled",
    )
    db.add(other)
    db.commit()
    assert "overlaps" in main.booking_availability_error(
        requested, 60, "secondary", exclude_event_id=target.id,
    )
    db.close()


def test_reschedule_moves_existing_event_in_place_and_rejects_cross_account(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    target = main.CalendarEvent(
        id="target-event",
        summary="Customer - Service (Anonymous)",
        customer_phone=thread.customer_phone,
        sms_account_key="secondary",
        thread_id=thread.id,
        start_time=datetime(2026, 9, 10, 21, 15),
        end_time=datetime(2026, 9, 10, 22, 15),
        status="scheduled",
    )
    db.add(target)
    db.commit()
    requested = datetime(2026, 9, 10, 21, 30, tzinfo=TZ)

    monkeypatch.setattr(main, "booking_availability_error", lambda *_args, **_kwargs: None)

    class Calendar:
        service = None

        def reschedule_booking(self, booking, start, end, account):
            assert booking.id == "target-event"
            assert account == "secondary"
            booking.start_time = start.replace(tzinfo=None)
            booking.end_time = end.replace(tzinfo=None)
            return True

    monkeypatch.setattr(main, "calendar_service", Calendar())
    result = main.reschedule_conversational_booking(
        db, thread, target, requested,
        {"correlation_id": "correlation", "source_message_id": "source", "timezone": "Australia/Hobart"},
    )
    assert result["status"] == "rescheduled"
    assert target.id == "target-event"
    assert target.start_time == requested.replace(tzinfo=None)
    assert db.query(main.CalendarEvent).count() == 1

    target.sms_account_key = "primary"
    before = target.start_time
    rejected = main.reschedule_conversational_booking(
        db, thread, target, requested + timedelta(minutes=15),
        {"correlation_id": "correlation", "source_message_id": "source", "timezone": "Australia/Hobart"},
    )
    assert rejected["status"] == "rejected"
    assert target.start_time == before
    db.close()


def test_failed_calendar_move_preserves_original_booking(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    target = main.CalendarEvent(
        id="target-event", summary="Booking", customer_phone=thread.customer_phone,
        sms_account_key="secondary", thread_id=thread.id,
        start_time=datetime(2026, 9, 10, 21, 15),
        end_time=datetime(2026, 9, 10, 22, 15), status="scheduled",
    )
    db.add(target)
    db.commit()
    monkeypatch.setattr(main, "booking_availability_error", lambda *_args, **_kwargs: None)

    class FailedCalendar:
        service = object()

        def reschedule_booking(self, *_args, **_kwargs):
            return False

    monkeypatch.setattr(main, "calendar_service", FailedCalendar())
    result = main.reschedule_conversational_booking(
        db, thread, target, datetime(2026, 9, 10, 21, 30, tzinfo=TZ),
        {"correlation_id": "correlation", "source_message_id": "source", "timezone": "Australia/Hobart"},
    )
    assert result["status"] == "rejected"
    assert target.start_time == datetime(2026, 9, 10, 21, 15)
    db.close()


def test_live_reply_reschedules_in_place_without_model_or_duplicate_booking(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    target = main.CalendarEvent(
        id="target-event", summary="Customer - Service (Anonymous)",
        customer_phone=thread.customer_phone, sms_account_key="secondary",
        thread_id=thread.id, start_time=datetime(2026, 9, 10, 21, 15),
        end_time=datetime(2026, 9, 10, 22, 15), status="scheduled",
    )
    customer = main.Message(
        id="customer-move", thread_id=thread.id, role="customer",
        text="Can you make it 9:30pm so I'm not late?",
        provider_message_id="provider-move", at=datetime(2026, 9, 9, 12, 0),
    )
    db.add_all([target, customer])
    db.commit()

    sent = []

    class LocalCalendar:
        service = None

        def get_busy_slots_for_account(self, _start, _end, _account, **kwargs):
            return [] if kwargs.get("exclude_event_id") == "target-event" else [
                {
                    "start": datetime(2026, 9, 10, 21, 15, tzinfo=TZ),
                    "end": datetime(2026, 9, 10, 22, 15, tzinfo=TZ),
                },
            ]

        def get_customer_bookings(self, *_args, **_kwargs):
            return [{
                "id": target.id, "summary": target.summary,
                "start": target.start_time.replace(tzinfo=TZ),
                "end": target.end_time.replace(tzinfo=TZ),
            }]

        def reschedule_booking(self, booking, start, end, _account):
            booking.start_time = start.replace(tzinfo=None)
            booking.end_time = end.replace(tzinfo=None)
            return True

    monkeypatch.setattr(main, "calendar_service", LocalCalendar())
    monkeypatch.setattr(main, "resolve_provider_context", lambda account: {"provider_name": account})
    monkeypatch.setattr(main, "account_allows_conversational_ai", lambda _account: True)
    monkeypatch.setattr(main, "current_business_time", lambda: datetime(2026, 9, 9, 22, 0, tzinfo=TZ))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": "Thursday", "enabled": True, "open": "00:00", "close": "23:59"},
    ])
    monkeypatch.setattr(main, "build_authority_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda *_args, **_kwargs: sent.append(_args[1]) or {"status": "success"})

    class NoModel:
        class Responses:
            def create(self, **_kwargs):
                raise AssertionError("deterministic rescheduling must not ask the model to mutate a booking")

        responses = Responses()

    monkeypatch.setattr(main, "openai_client", NoModel())

    assert main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
    ) == (False, False)
    db.refresh(target)
    assert target.start_time == datetime(2026, 9, 10, 21, 30)
    assert target.end_time == datetime(2026, 9, 10, 22, 30)
    assert db.query(main.CalendarEvent).count() == 1
    assert sent == ["All good, I've moved it to Thursday at 9:30pm."]
    assert db.query(main.ThreadEvent).filter(main.ThreadEvent.type == "booking_rescheduled").count() == 1
    db.close()

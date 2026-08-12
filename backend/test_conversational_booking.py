import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from booking_tools import BOOKING_DISCOVERY_TOOL_SCHEMAS
from main import (
    Base,
    Message,
    Thread,
    confirm_conversational_booking,
    current_business_time,
    is_explicit_booking_confirmation,
    is_explicit_booking_rejection,
    propose_conversational_booking,
    run_sms_reply_logic,
)


class FakeCalendar:
    def __init__(self):
        self.busy = []
        self.existing = []
        self.created = []

    def get_busy_slots(self, start, end):
        return self.busy

    def get_customer_bookings(self, phone, start, end, db=None):
        return self.existing

    def create_booking(self, summary, start, end, customer_phone):
        self.created.append({
            "summary": summary,
            "start": start,
            "end": end,
            "customer_phone": customer_phone,
        })
        return True


class FakeFunctionCall:
    type = "function_call"

    def __init__(self, name, arguments, call_id):
        self.name = name
        self.arguments = json.dumps(arguments)
        self.call_id = call_id


class FakeResponse:
    def __init__(self, *, output=None, output_text=""):
        self.output = output or []
        self.output_text = output_text


class SequenceClient:
    def __init__(self, responses):
        self.responses = self
        self.pending = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.pending.pop(0)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_thread(db):
    now = main.datetime.utcnow()
    thread = Thread(
        id="conversational-booking-thread",
        customer_phone="+61412345678",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.flush()
    return thread


@pytest.mark.parametrize("message", [
    "yes",
    "Yes please",
    "that's correct",
    "confirm it please",
    "go ahead",
    "book it",
])
def test_explicit_confirmation_phrases(message):
    assert is_explicit_booking_confirmation(message) is True


@pytest.mark.parametrize("message", [
    "maybe",
    "yes but make it 4pm",
    "that might be right",
    "what was the price?",
    "no",
])
def test_ambiguous_or_changed_details_are_not_confirmation(message):
    assert is_explicit_booking_confirmation(message) is False


@pytest.mark.parametrize("message", ["no", "No thanks", "cancel it", "that's wrong"])
def test_explicit_rejection_phrases_clear_authorization(message):
    assert is_explicit_booking_rejection(message) is True


def test_proposal_then_later_confirmation_books_without_a_web_form(tmp_path, monkeypatch):
    service = {
        "id": "massage-60",
        "name": "60 minute massage",
        "duration": 60,
        "price": 200,
    }
    (tmp_path / "services.json").write_text(json.dumps([service]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)

    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )

    proposal_result = propose_conversational_booking(
        thread,
        service_id="massage-60",
        start_time=start.isoformat(),
        customer_name="Example Customer",
        notes="First visit",
    )

    assert proposal_result["status"] == "awaiting_confirmation"
    assert thread.pending_booking is None
    assert calendar.created == []
    thread.pending_booking = json.dumps(proposal_result["proposal"])

    confirmation_result, confirmed = confirm_conversational_booking(db, thread, "Yes, please")

    assert confirmed is True
    assert confirmation_result["status"] == "confirmed"
    assert thread.pending_booking is None
    assert len(calendar.created) == 1
    assert calendar.created[0]["summary"] == "Example Customer - 60 minute massage"
    assert calendar.created[0]["end"] - calendar.created[0]["start"] == timedelta(minutes=60)
    db.close()


def test_confirmation_is_rejected_if_customer_changes_the_details(tmp_path, monkeypatch):
    (tmp_path / "services.json").write_text(
        '[{"id":"service","name":"Service","duration":30,"price":100}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)
    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    proposal_result = propose_conversational_booking(
        thread,
        service_id="service",
        start_time=start.isoformat(),
        customer_name="Example Customer",
        notes=None,
    )
    thread.pending_booking = json.dumps(proposal_result["proposal"])

    result, confirmed = confirm_conversational_booking(
        db,
        thread,
        "Yes but make it 4pm",
    )

    assert confirmed is False
    assert result["status"] == "rejected"
    assert thread.pending_booking is not None
    assert calendar.created == []
    db.close()


def test_availability_is_rechecked_after_confirmation(tmp_path, monkeypatch):
    (tmp_path / "services.json").write_text(
        '[{"id":"service","name":"Service","duration":30,"price":100}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)
    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    proposal_result = propose_conversational_booking(
        thread,
        service_id="service",
        start_time=start.isoformat(),
        customer_name="Example Customer",
        notes=None,
    )
    thread.pending_booking = json.dumps(proposal_result["proposal"])
    calendar.busy = [{"start": start, "end": start + timedelta(minutes=30)}]

    result, confirmed = confirm_conversational_booking(db, thread, "yes")

    assert confirmed is False
    assert result == {"status": "rejected", "reason": "That time is no longer available."}
    assert calendar.created == []
    assert thread.pending_booking is None
    db.close()


def test_live_reply_flow_proposes_then_confirms_on_the_next_customer_turn(tmp_path, monkeypatch):
    service = {"id": "service", "name": "Service", "duration": 30, "price": 100}
    (tmp_path / "services.json").write_text(json.dumps([service]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)
    monkeypatch.setattr(main, "build_business_context", lambda query: main.get_live_services_context())
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    first_customer = Message(
        id="proposal-message",
        thread_id=thread.id,
        role="customer",
        text="Book Service at 2pm. My name is Example Customer.",
        provider_message_id="proposal-provider-id",
        at=main.datetime.utcnow(),
    )
    db.add(first_customer)
    db.commit()
    proposal_client = SequenceClient([
        FakeResponse(output=[FakeFunctionCall(
            "propose_booking",
            {
                "service_id": "service",
                "start_time": start.isoformat(),
                "customer_name": "Example Customer",
                "notes": None,
            },
            "proposal-call",
        )]),
        FakeResponse(output_text="Service for Example Customer at 2:00 PM, 30 minutes. Is that correct?"),
    ])
    monkeypatch.setattr(main, "openai_client", proposal_client)

    booked, _ = run_sms_reply_logic(
        db,
        thread.id,
        first_customer.text,
        first_customer.provider_message_id,
        first_customer.at,
        dispatch_sms=False,
    )

    db.refresh(thread)
    assert booked is False
    assert thread.pending_booking is not None
    assert calendar.created == []

    confirmation = Message(
        id="confirmation-message",
        thread_id=thread.id,
        role="customer",
        text="Yes, that's correct",
        provider_message_id="confirmation-provider-id",
        at=main.datetime.utcnow() + timedelta(seconds=1),
    )
    db.add(confirmation)
    db.commit()
    confirmation_client = SequenceClient([
        FakeResponse(output=[FakeFunctionCall("confirm_booking", {}, "confirmation-call")]),
        FakeResponse(output_text="Confirmed. Your Service booking is all set for 2:00 PM."),
    ])
    monkeypatch.setattr(main, "openai_client", confirmation_client)

    booked, _ = run_sms_reply_logic(
        db,
        thread.id,
        confirmation.text,
        confirmation.provider_message_id,
        confirmation.at,
        dispatch_sms=False,
    )

    db.refresh(thread)
    assert booked is True
    assert thread.pending_booking is None
    assert len(calendar.created) == 1
    assert calendar.created[0]["summary"] == "Example Customer - Service"
    assert "Pending conversational booking proposal" in json.dumps(
        confirmation_client.calls[0]["input"]
    )
    db.close()


def test_live_reply_flow_executes_booking_discovery_tool(monkeypatch):
    class FakeToolSuite:
        def execute(self, tool_name, arguments):
            assert tool_name == "get_current_time"
            assert arguments == {}
            return {
                "status": "ok",
                "timezone": "Australia/Hobart",
                "current_time": "2026-08-12T10:00:00+10:00",
            }

    monkeypatch.setattr(main, "get_booking_tool_suite", lambda: FakeToolSuite())
    monkeypatch.setattr(main, "build_business_context", lambda query: "")
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main.calendar_service, "get_busy_slots", lambda start, end: [])
    db = make_db()
    thread = add_thread(db)
    customer = Message(
        id="time-message",
        thread_id=thread.id,
        role="customer",
        text="What is the current time?",
        provider_message_id="time-provider-id",
        at=main.datetime.utcnow(),
    )
    db.add(customer)
    db.commit()
    client = SequenceClient([
        FakeResponse(output=[FakeFunctionCall("get_current_time", {}, "time-call")]),
        FakeResponse(output_text="It is 10:00 AM in Hobart."),
    ])
    monkeypatch.setattr(main, "openai_client", client)

    booked, _ = run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
        dispatch_sms=False,
    )

    assert booked is False
    assert "2026-08-12T10:00:00+10:00" in json.dumps(client.calls[1]["input"])
    db.close()


def test_assistant_uses_two_stage_booking_tools_and_no_form_link():
    source = Path(main.__file__).read_text(encoding="utf-8")
    discovery_tool_names = {tool["name"] for tool in BOOKING_DISCOVERY_TOOL_SCHEMAS}

    assert '"name": "propose_booking"' in source
    assert '"name": "confirm_booking"' in source
    assert discovery_tool_names == {
        "get_current_time",
        "list_booking_services",
        "get_times_today",
        "get_times_tomorrow",
        "get_next_available",
    }
    assert '"name": "create_booking_form_link"' not in source
    assert "pending_booking_at_turn_start" in source
    assert 'clean_body in ("1", "2", "3") and thread.pending_slots:' in source
    assert 'assistant_reply = f"All booked for' not in source

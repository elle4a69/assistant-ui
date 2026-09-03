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
    booking_availability_error,
    asks_for_secondary_booking_confirmation,
    confirm_conversational_booking,
    current_business_time,
    is_explicit_booking_confirmation,
    is_explicit_booking_rejection,
    propose_conversational_booking,
    run_sms_reply_logic,
    validate_availability_claim,
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
    "Yep looks good to me",
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


@pytest.mark.parametrize("message", [
    "Those are the details. Reply yes to confirm.",
    "Is that all correct?",
    "Please confirm those details.",
    "Would you like me to book that in?",
    "Shall I lock it in?",
])
def test_secondary_booking_confirmation_requests_are_prohibited(message):
    assert asks_for_secondary_booking_confirmation(message) is True


@pytest.mark.parametrize("message", [
    "What name should I put the booking under?",
    "Which service were you after?",
    "What time suits you?",
    "All good, see you then.",
])
def test_questions_for_missing_booking_details_remain_allowed(message):
    assert asks_for_secondary_booking_confirmation(message) is False


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

    confirmation_result, confirmed = confirm_conversational_booking(
        db, thread, "Yes, please", send_confirmation=False,
    )

    assert confirmed is True
    assert confirmation_result["status"] == "confirmed"
    assert thread.pending_booking is None
    assert len(calendar.created) == 1
    assert calendar.created[0]["summary"] == "Example Customer - 60 minute massage (Tori)"
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
    assert result == {
        "status": "rejected",
        "reason": "That time overlaps another booking.",
    }
    assert calendar.created == []
    assert thread.pending_booking is None
    db.close()


def test_proposal_fails_closed_when_live_calendar_cannot_be_verified(tmp_path, monkeypatch):
    (tmp_path / "services.json").write_text(
        '[{"id":"service","name":"Service","duration":30,"price":100}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])

    class UnavailableCalendar(FakeCalendar):
        def get_busy_slots_authoritative(self, start, end):
            raise OSError("calendar unavailable")

    calendar = UnavailableCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)
    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )

    result = propose_conversational_booking(
        thread,
        service_id="service",
        start_time=start.isoformat(),
        customer_name="Example Customer",
        notes=None,
    )

    assert result == {
        "status": "rejected",
        "reason": "Live calendar availability could not be verified. No booking was made.",
    }
    assert calendar.created == []
    db.close()


def test_live_reply_flow_books_immediately_when_details_are_complete(tmp_path, monkeypatch):
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
    class LiveSlotSuite:
        def execute(self, tool_name, arguments):
            assert tool_name == "get_times_today"
            assert arguments == {"service_id": "service"}
            return {"status": "ok", "service_id": "service", "slots": [{
                "service_id": "service",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(minutes=30)).isoformat(),
            }]}

    monkeypatch.setattr(main, "get_booking_tool_suite", lambda: LiveSlotSuite())
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
            "get_times_today", {"service_id": "service"}, "availability-call",
        )]),
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
        FakeResponse(output_text="All good, see you then."),
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
    assert booked is True
    assert thread.pending_booking is None
    assert len(calendar.created) == 1
    assert calendar.created[0]["summary"] == "Example Customer - Service (Tori)"
    confirmation_reply = db.query(Message).filter(
        Message.thread_id == thread.id,
        Message.role.in_(["agent", "draft", "system"]),
    ).order_by(Message.at.desc()).first()
    assert confirmation_reply is not None
    assert "When you arrive, tap:" in confirmation_reply.text
    assert "/a/" in confirmation_reply.text
    assert "Is that correct" not in confirmation_reply.text
    assert len(proposal_client.calls) == 3
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


def test_live_reply_flow_can_discover_then_propose_in_separate_tool_rounds(tmp_path, monkeypatch):
    service = {"id": "service", "name": "Service", "duration": 60, "price": 200}
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
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )

    class ExactSlotSuite:
        def execute(self, tool_name, arguments):
            assert tool_name == "get_times_today"
            assert arguments == {"service_id": "service"}
            return {
                "status": "ok",
                "service_id": "service",
                "slots": [{
                    "service_id": "service",
                    "start_time": start.isoformat(),
                    "end_time": (start + timedelta(minutes=60)).isoformat(),
                }],
            }

    monkeypatch.setattr(main, "get_booking_tool_suite", lambda: ExactSlotSuite())
    db = make_db()
    thread = add_thread(db)
    customer = Message(
        id="discover-then-propose-message",
        thread_id=thread.id,
        role="customer",
        text="Book the one hour Service at 2pm. My name is Example Customer.",
        provider_message_id="discover-then-propose-provider",
        at=main.datetime.utcnow(),
    )
    db.add(customer)
    db.commit()
    client = SequenceClient([
        FakeResponse(output=[FakeFunctionCall(
            "get_times_today", {"service_id": "service"}, "availability-call",
        )]),
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
        FakeResponse(output_text="All good, see you then."),
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

    db.refresh(thread)
    assert booked is True
    assert thread.pending_booking is None
    assert len(client.calls) == 3
    assert all("tools" in call for call in client.calls[:2])
    assert len(calendar.created) == 1
    db.close()


def test_proposal_without_verified_live_time_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "build_business_context", lambda query: "")
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    customer = Message(
        id="unverified-proposal-message",
        thread_id=thread.id,
        role="customer",
        text="Book it for 2pm. My name is Example Customer.",
        provider_message_id="unverified-proposal-provider",
        at=main.datetime.utcnow(),
    )
    db.add(customer)
    db.commit()
    client = SequenceClient([
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
        FakeResponse(output_text="I need to check that time before I can prepare the booking."),
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

    db.refresh(thread)
    assert booked is False
    assert thread.pending_booking is None
    tool_output = json.dumps(client.calls[1]["input"])
    assert "live availability must be checked first" in tool_output
    db.close()


def test_simulator_retains_proposal_while_training_mode_creates_a_draft(tmp_path, monkeypatch):
    service = {"id": "service", "name": "Service", "duration": 30, "price": 100}
    (tmp_path / "services.json").write_text(json.dumps([service]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    monkeypatch.setattr(main, "calendar_service", FakeCalendar())
    monkeypatch.setattr(main, "build_business_context", lambda query: main.get_live_services_context())
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", True)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    db = make_db()
    thread = add_thread(db)
    class LiveSlotSuite:
        def execute(self, tool_name, arguments):
            assert tool_name == "get_times_today"
            assert arguments == {"service_id": "service"}
            return {"status": "ok", "service_id": "service", "slots": [{
                "service_id": "service",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(minutes=30)).isoformat(),
            }]}

    monkeypatch.setattr(main, "get_booking_tool_suite", lambda: LiveSlotSuite())
    customer = Message(
        id="simulator-proposal-message",
        thread_id=thread.id,
        role="customer",
        text="Book that Service at 2pm. My name is Example Customer.",
        provider_message_id="simulator-proposal-provider",
        at=main.datetime.utcnow(),
    )
    db.add(customer)
    db.commit()
    client = SequenceClient([
        FakeResponse(output=[FakeFunctionCall(
            "get_times_today", {"service_id": "service"}, "availability-call",
        )]),
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
        FakeResponse(output_text="Service for Example Customer at 2:00 PM for 30 minutes. Is that correct?"),
    ])
    monkeypatch.setattr(main, "openai_client", client)

    run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
        dispatch_sms=False,
        is_simulation=True,
    )

    db.refresh(thread)
    assert thread.pending_booking is not None
    assert db.query(Message).filter(Message.role == "draft").count() == 1
    db.close()


def test_one_hour_slot_cannot_be_described_as_thirty_minutes():
    now_local = main.parse_business_datetime("2026-08-13T12:00:00+10:00")
    exact_hour_slot = [{
        "service_id": "pse-60",
        "start": "2026-08-13T13:30:00+10:00",
        "end": "2026-08-13T14:30:00+10:00",
    }]

    assert validate_availability_claim(
        "I can't do a full hour at 1:30pm, only 30 minutes.",
        exact_hour_slot,
        60,
        now_local,
    ) == "AI said a provider-validated exact-duration slot was unavailable"
    assert validate_availability_claim(
        "I don\u2019t have a full hour at 1:30pm, only 30 minutes.",
        exact_hour_slot,
        60,
        now_local,
    ) == "AI said a provider-validated exact-duration slot was unavailable"
    assert validate_availability_claim(
        "I can do the one-hour PSE at 1:30pm.",
        exact_hour_slot,
        60,
        now_local,
    ) is None


def test_thirty_minute_tool_result_cannot_support_one_hour_claim():
    now_local = main.parse_business_datetime("2026-08-13T12:00:00+10:00")
    short_slot = [{
        "service_id": "pse-30",
        "start": "2026-08-13T13:30:00+10:00",
        "end": "2026-08-13T14:00:00+10:00",
    }]

    assert validate_availability_claim(
        "I can do the one-hour PSE at 1:30pm.",
        short_slot,
        60,
        now_local,
    ) == "AI claimed an exact time without matching exact-duration provider evidence"


def test_ai_cannot_assemble_two_short_bookings_to_fake_long_service():
    now_local = main.parse_business_datetime("2026-08-13T12:00:00+10:00")

    assert validate_availability_claim(
        "I can do two back-to-back 30 minute PSE slots at 1:30pm and 2pm.",
        [],
        60,
        now_local,
    ) == "AI attempted to combine separate short appointments into a longer service"


def test_live_flow_rejects_contradiction_of_one_hour_provider_slot(monkeypatch):
    class ExactHourSuite:
        def execute(self, tool_name, arguments):
            assert tool_name == "get_times_today"
            assert arguments == {"service_id": "pse-60"}
            return {
                "status": "ok",
                "service_id": "pse-60",
                "slots": [{
                    "service_id": "pse-60",
                    "start_time": "2026-08-13T13:30:00+10:00",
                    "end_time": "2026-08-13T14:30:00+10:00",
                }],
            }

    db = make_db()
    thread = add_thread(db)
    customer = Message(
        id="pse-hour-message",
        thread_id=thread.id,
        role="customer",
        text="I want the one hour PSE. Can you do 1:30pm?",
        provider_message_id="pse-hour-provider",
        at=main.datetime.utcnow(),
    )
    db.add(customer)
    db.commit()
    monkeypatch.setattr(main, "get_booking_tool_suite", lambda: ExactHourSuite())
    monkeypatch.setattr(main, "build_business_context", lambda _query: "")
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(
        main,
        "current_business_time",
        lambda: main.parse_business_datetime("2026-08-13T12:00:00+10:00"),
    )
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])
    client = SequenceClient([
        FakeResponse(output=[FakeFunctionCall("get_times_today", {"service_id": "pse-60"}, "availability-call")]),
        FakeResponse(output_text="I can't do a full hour at 1:30pm, only 30 minutes."),
    ])
    monkeypatch.setattr(main, "openai_client", client)

    main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
        dispatch_sms=False,
    )

    db.refresh(thread)
    assert thread.state == "needs-review"
    assert db.query(Message).filter(Message.role.in_(["system", "draft"])).count() == 0
    failure = db.query(main.ThreadEvent).filter(main.ThreadEvent.type == "ai-reply-failed").one()
    assert "provider-validated exact-duration slot" in json.loads(failure.meta)["reason"]
    db.close()


def test_assistant_uses_immediate_booking_tool_and_no_form_or_confirmation_tool():
    source = Path(main.__file__).read_text(encoding="utf-8")
    discovery_tool_names = {tool["name"] for tool in BOOKING_DISCOVERY_TOOL_SCHEMAS}
    schema_start = source.index("flat_tools = [")
    schema_end = source.index("examples = []", schema_start)
    live_tool_schema = source[schema_start:schema_end]

    assert '"name": "propose_booking"' in live_tool_schema
    assert '"name": "confirm_booking"' not in live_tool_schema
    assert discovery_tool_names == {
        "get_current_time",
        "list_booking_services",
        "get_times_today",
        "get_times_tomorrow",
        "get_next_available",
    }
    assert '"name": "create_booking_form_link"' not in source
    assert "pending_booking_at_turn_start" in source
    assert "Hard calendar authority" in source
    assert 'tool_choice="required" if booking_or_availability_turn else "auto"' in source
    assert "never ask for a secondary confirmation" in source
    assert "thread.pending_slots = None" in source
    assert "pending_slots = json.dumps(verified_slots)" not in source
    assert 'assistant_reply = f"All booked for' not in source


def test_secondary_confirmation_is_retried_as_a_required_booking_tool_call(tmp_path, monkeypatch):
    service = {"id": "service", "name": "Service", "duration": 30, "price": 100}
    (tmp_path / "services.json").write_text(json.dumps([service]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )

    class LiveSlotSuite:
        def execute(self, tool_name, arguments):
            assert tool_name == "get_times_today"
            return {"status": "ok", "service_id": "service", "slots": [{
                "service_id": "service",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(minutes=30)).isoformat(),
            }]}

    monkeypatch.setattr(main, "get_booking_tool_suite", lambda: LiveSlotSuite())
    db = make_db()
    thread = add_thread(db)
    customer = Message(
        id="no-secondary-confirmation-message",
        thread_id=thread.id,
        role="customer",
        text="Book Service at 2pm. My name is Example Customer.",
        provider_message_id="no-secondary-confirmation-provider",
        at=main.datetime.utcnow(),
    )
    db.add(customer)
    db.commit()
    client = SequenceClient([
        FakeResponse(output_text="Service at 2pm. Reply yes to confirm."),
        FakeResponse(output=[FakeFunctionCall(
            "get_times_today", {"service_id": "service"}, "availability-call",
        )]),
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
        FakeResponse(output_text="All good, see you then."),
    ])
    monkeypatch.setattr(main, "openai_client", client)

    booked, _ = run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id,
        customer.at, dispatch_sms=False,
    )

    assert booked is True
    assert len(calendar.created) == 1
    assert client.calls[0]["tool_choice"] == "required"
    assert client.calls[1]["tool_choice"] == "required"
    assert all(
        not asks_for_secondary_booking_confirmation(message.text)
        for message in db.query(Message).filter(Message.role.in_(["agent", "draft"])).all()
    )
    db.close()


def test_stored_availability_is_discarded_and_cannot_be_sent_without_live_lookup(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    thread.pending_slots = json.dumps([{
        "service_id": "old-service",
        "start": "2026-01-01T15:00:00+11:00",
        "end": "2026-01-01T15:30:00+11:00",
    }])
    customer = Message(
        id="stale-availability-message",
        thread_id=thread.id,
        role="customer",
        text="Are you free at 3pm?",
        provider_message_id="stale-availability-provider",
        at=main.datetime.utcnow(),
    )
    db.add(customer)
    db.commit()
    monkeypatch.setattr(main, "build_business_context", lambda _query: (_ for _ in ()).throw(
        AssertionError("availability turns must not retrieve stored knowledge"),
    ))
    monkeypatch.setattr(main, "openai_client", SequenceClient([
        FakeResponse(output_text="I am available at 3pm."),
    ]))

    assert run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
        dispatch_sms=False,
    ) == (False, False)

    db.refresh(thread)
    assert thread.pending_slots is None
    assert thread.state == "needs-review"
    assert db.query(Message).filter(Message.role.in_(["system", "draft"])).count() == 0
    failure = db.query(main.ThreadEvent).filter(main.ThreadEvent.type == "ai-reply-failed").one()
    assert "fresh live calendar lookup" in json.loads(failure.meta)["reason"]
    db.close()


def test_booking_allows_immediate_and_back_to_back_times_but_rejects_overlap(monkeypatch):
    now = main.parse_business_datetime("2026-08-12T10:00:00+10:00")
    monkeypatch.setattr(main, "current_business_time", lambda: now)
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)

    assert booking_availability_error(now + timedelta(minutes=1), 30) is None
    assert booking_availability_error(now - timedelta(minutes=1), 30) == "Bookings cannot be made in the past."
    candidate = now + timedelta(hours=2)
    calendar.busy = [{"start": candidate - timedelta(minutes=40), "end": candidate - timedelta(minutes=10)}]
    assert booking_availability_error(candidate, 30) is None
    calendar.busy = [{"start": candidate + timedelta(minutes=40), "end": candidate + timedelta(minutes=70)}]
    assert booking_availability_error(candidate, 30) is None
    calendar.busy = [{"start": candidate + timedelta(minutes=15), "end": candidate + timedelta(minutes=45)}]
    assert booking_availability_error(candidate, 30) == "That time overlaps another booking."


def test_conversational_booking_uses_saved_system_confirmation_template(tmp_path, monkeypatch):
    service = {"id": "service", "name": "Service", "duration": 30, "price": 100}
    (tmp_path / "services.json").write_text(json.dumps([service]), encoding="utf-8")
    (tmp_path / "sms_confirmation_template.txt").write_text(
        "Address: {address}\n{name} — {service} — {time}\n{arrival_link}",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "PROMPTS_DIR", str(tmp_path))
    monkeypatch.setattr(main, "get_business_variable_values", lambda: {"address": "Example Address"})
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)
    sent = []
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda *args, **kwargs: sent.append((args, kwargs)) or {"status": "success"})
    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
    proposal = propose_conversational_booking(
        thread, service_id="service", start_time=start.isoformat(), customer_name="Example Customer", notes=None,
    )
    thread.pending_booking = json.dumps(proposal["proposal"])

    result, confirmed = confirm_conversational_booking(db, thread, "yes")

    assert confirmed is True
    assert result["booking"]["booking_confirmation_handled"] is True
    assert len(sent) == 1
    assert "Example Address" in sent[0][0][1]
    assert "/a/" in sent[0][0][1]
    assert db.query(Message).filter(Message.role == "agent").count() == 1
    db.close()


def test_explicit_confirmation_of_a_legacy_pending_booking_writes_without_model_tool(tmp_path, monkeypatch):
    service = {"id": "service", "name": "Service", "duration": 30, "price": 100}
    (tmp_path / "services.json").write_text(json.dumps([service]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "load_working_hours", lambda: [
        {"day": day, "enabled": True, "open": "00:00", "close": "23:59"}
        for day in main.DAY_NAMES
    ])
    calendar = FakeCalendar()
    monkeypatch.setattr(main, "calendar_service", calendar)
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    db = make_db()
    thread = add_thread(db)
    start = (current_business_time() + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    proposal = propose_conversational_booking(
        thread,
        service_id="service",
        start_time=start.isoformat(),
        customer_name="Example Customer",
        notes=None,
    )
    thread.pending_booking = json.dumps(proposal["proposal"])
    confirmation = Message(
        id="legacy-confirmation-message",
        thread_id=thread.id,
        role="customer",
        text="Yep, looks good to me",
        provider_message_id="legacy-confirmation-provider",
        at=main.datetime.utcnow(),
    )
    db.add(confirmation)
    db.commit()
    monkeypatch.setattr(main, "openai_client", SequenceClient([
        FakeResponse(output_text="All good, see you then."),
    ]))

    booked, _ = run_sms_reply_logic(
        db, thread.id, confirmation.text, confirmation.provider_message_id,
        confirmation.at, dispatch_sms=False,
    )

    db.refresh(thread)
    assert booked is True
    assert thread.pending_booking is None
    assert len(calendar.created) == 1
    db.close()


def test_calendar_only_validator_requires_a_fresh_lookup():
    assert main.validate_calendar_only_reply(
        "I have an opening at 3pm.", live_lookup_succeeded=False,
    ) == "AI stated availability without a fresh live calendar lookup"
    assert main.validate_calendar_only_reply(
        "I have an opening at 3pm.", live_lookup_succeeded=True,
    ) is None


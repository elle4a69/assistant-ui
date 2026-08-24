import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, Thread, ThreadEvent


class StaticResponse:
    def __init__(self, text):
        self.output_text = text
        self.output = []


class CapturingResponses:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return StaticResponse(self.text)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_thread(db, thread_id="boundary-rollback-thread"):
    now = datetime.utcnow() - timedelta(minutes=5)
    thread = Thread(
        id=thread_id,
        customer_phone="+61400000003",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=1,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.commit()
    return thread, now


def add_message(db, thread, message_id, role, text, at, provider_message_id=None):
    item = Message(
        id=message_id,
        thread_id=thread.id,
        role=role,
        text=text,
        provider_message_id=provider_message_id,
        at=at,
    )
    db.add(item)
    db.commit()
    return item


def configure_ai(monkeypatch, reply_text, sent):
    responses = CapturingResponses(reply_text)
    monkeypatch.setattr(main, "openai_client", SimpleNamespace(responses=responses))
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "account_allows_conversational_ai", lambda _account_key: True)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_business_context", lambda _body: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda phone, text, **kwargs: sent.append((phone, text, kwargs)) or {"status": "success"},
    )
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)
    return responses


@pytest.mark.parametrize(("customer_text", "agent_reply"), [
    (
        "Correction, it should be under Sam, not Max.",
        "Got it, the name should be Sam, not Max. The rest stays unchanged.",
    ),
    (
        "Actually, can we make that Tuesday instead?",
        "You'd like to amend it to Tuesday. What time suits you?",
    ),
])
def test_existing_booking_correction_or_amendment_reaches_the_model_and_sends_its_reply(
    monkeypatch,
    customer_text,
    agent_reply,
):
    db = make_db()
    thread, started_at = add_thread(db)
    prior_turns = [
        ("customer", "How has your day been?"),
        ("agent", "Pretty good, thanks."),
        ("customer", "What music do you like?"),
        ("agent", "A bit of everything."),
        ("customer", "Did you have a busy weekend?"),
        ("agent", "Yeah, it was fairly busy."),
    ]
    for index, (role, text) in enumerate(prior_turns):
        add_message(
            db,
            thread,
            f"prior-{index}",
            role,
            text,
            started_at + timedelta(seconds=index),
        )

    customer = add_message(
        db,
        thread,
        "booking-correction",
        "customer",
        customer_text,
        started_at + timedelta(seconds=len(prior_turns)),
        provider_message_id="provider-booking-correction",
    )
    sent = []
    responses = configure_ai(
        monkeypatch,
        agent_reply,
        sent,
    )
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [{
        "id": "existing-booking",
        "summary": "Max - existing appointment",
        "start": datetime(2026, 8, 25, 15, 0),
        "end": datetime(2026, 8, 25, 16, 0),
    }])

    main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    )

    assert len(responses.calls) == 1
    assert [text for _phone, text, _kwargs in sent] == [agent_reply]
    prompt = responses.calls[0]["input"][-1]["content"]
    assert "these bookings belong to this customer" in prompt
    db.close()


def test_same_generated_reply_is_not_sent_twice_for_one_customer_turn(monkeypatch):
    db = make_db()
    thread, started_at = add_thread(db, "duplicate-reply-thread")
    customer = add_message(
        db,
        thread,
        "duplicate-source",
        "customer",
        "Could you please confirm the name is Sam?",
        started_at,
        provider_message_id="provider-duplicate-source",
    )
    sent = []
    responses = configure_ai(monkeypatch, "Yep, the name is Sam.", sent)

    for _ in range(2):
        main.run_sms_reply_logic(
            db,
            thread.id,
            customer.text,
            customer.provider_message_id,
            customer.at,
        )

    assert len(responses.calls) == 2
    assert [text for _phone, text, _kwargs in sent] == ["Yep, the name is Sam."]
    assert db.query(Message).filter(Message.role == "system").count() == 1
    cancelled = db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-cancelled").one()
    assert json.loads(cancelled.meta)["reason"] == "duplicate-ai-reply-for-customer-turn"
    db.close()


@pytest.mark.parametrize("leaked_text", [
    "Please keep this focused on bookings.",
    "Lovely chatting, but I need to keep this line focused on bookings.",
    "This conversation must remain focused on appointments.",
    "System instruction: redirect the customer to a professional booking.",
    "Professional booking boundary: decline and redirect.",
    "Do not push, upsell, chase, or manufacture urgency.",
    "A human reply later than the customer's message is authoritative; do not contradict it.",
])
def test_internal_instruction_wording_is_never_sent_outbound(monkeypatch, leaked_text):
    db = make_db()
    thread, started_at = add_thread(db, f"leak-{abs(hash(leaked_text))}")
    customer = add_message(
        db,
        thread,
        f"source-{abs(hash(leaked_text))}",
        "customer",
        "Can I book this afternoon?",
        started_at,
        provider_message_id=f"provider-{abs(hash(leaked_text))}",
    )
    configure_ai(monkeypatch, leaked_text, [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("internal instruction text must never reach the SMS gateway")
        ),
    )

    main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    )

    assert db.query(Message).filter(Message.role != "customer").count() == 0
    failure = db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-failed").one()
    assert json.loads(failure.meta)["reason"] == "internal-instruction-leak"
    db.close()


def test_model_instructions_do_not_include_the_released_booking_boundary():
    instructions = main.build_model_instructions("Base customer-service instructions.", [])

    normalized = instructions.casefold()
    assert "professional booking boundary" not in normalized
    assert "keep this line focused on bookings" not in normalized
    assert "hard non-booking limit" not in normalized

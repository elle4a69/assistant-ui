import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, CalendarEvent, Message, Thread, ThreadEvent


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


def add_message(db, thread_id, role, text, at, message_id):
    message = Message(
        id=message_id,
        thread_id=thread_id,
        role=role,
        text=text,
        provider_message_id=f"provider-{message_id}" if role == "customer" else None,
        at=at,
    )
    db.add(message)
    return message


def configure_reply_flow(monkeypatch, model_text, qa_reply=None):
    responses = CapturingResponses(model_text)
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: qa_reply)
    monkeypatch.setattr(main, "build_business_context", lambda _body: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)
    return responses


def test_booking_boundary_policy_is_not_in_model_instructions():
    instructions = main.build_model_instructions("Be helpful and context-aware.", [])

    assert "Professional booking boundary" not in instructions
    assert "keep this focused on bookings" not in instructions.casefold()
    assert "keep this line focused on bookings" not in instructions.casefold()
    assert main.BOOKING_AVAILABILITY_SAFETY_POLICY in instructions


@pytest.mark.parametrize("reply_source", ["model", "qa-rule"])
def test_internal_booking_focus_wording_cannot_be_sent(reply_source, monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    thread = Thread(
        id=f"leak-{reply_source}",
        customer_phone="+61400000001",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=1,
        created_at=now,
        updated_at=now,
    )
    customer = add_message(
        db, thread.id, "customer", "Can you update my booking?", now, f"customer-{reply_source}",
    )
    db.add(thread)
    db.commit()
    leaked_reply = "Lovely chatting, but I need to keep this line focused on bookings."
    responses = configure_reply_flow(
        monkeypatch,
        leaked_reply,
        qa_reply=leaked_reply if reply_source == "qa-rule" else None,
    )
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("internal instruction text must not be sent")
        ),
    )

    main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
    )

    assert len(responses.calls) == (1 if reply_source == "model" else 0)
    assert db.query(Message).filter(Message.role != "customer").count() == 0
    failure = db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-failed").one()
    assert json.loads(failure.meta)["reason"] == "internal-instruction-text"
    assert db.get(Thread, thread.id).state == "needs-review"
    db.close()


@pytest.mark.parametrize(
    ("customer_request", "normal_reply"),
    [
        (
            "Actually, I need to correct the name on my active booking.",
            "Of course. What name should I update the booking to?",
        ),
        (
            "Can we amend my booking to a different time?",
            "Yes, I can help amend it. What day and time would you prefer?",
        ),
    ],
)
def test_booking_corrections_and_amendments_use_context_aware_ai(
    customer_request, normal_reply, monkeypatch,
):
    db = make_db()
    now = datetime.utcnow()
    thread = Thread(
        id="amendment-thread",
        customer_phone="+61400000002",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=1,
        created_at=now - timedelta(minutes=10),
        updated_at=now,
    )
    db.add(thread)
    history = [
        ("customer", "How has your day been?"),
        ("system", "Good, thanks."),
        ("customer", "What music do you like?"),
        ("system", "A bit of everything."),
        ("customer", "Tell me something else."),
        ("system", "What would you like to know?"),
    ]
    for index, (role, text) in enumerate(history):
        add_message(
            db,
            thread.id,
            role,
            text,
            now - timedelta(minutes=9 - index),
            f"history-{index}",
        )
    customer = add_message(
        db, thread.id, "customer", customer_request, now, "amendment-customer",
    )
    db.add(CalendarEvent(
        id="active-booking",
        customer_phone=thread.customer_phone,
        summary="Existing booking",
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, hours=1),
    ))
    db.commit()
    responses = configure_reply_flow(monkeypatch, normal_reply)
    sent = []
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda phone, text, **kwargs: sent.append((phone, text, kwargs)) or {"status": "success"},
    )

    main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
    )

    assert len(responses.calls) == 1
    assert "these bookings belong to this customer" in responses.calls[0]["input"][-1]["content"]
    assert sent[0][1] == normal_reply
    assert "focused on bookings" not in sent[0][1].casefold()
    assert db.query(ThreadEvent).filter(ThreadEvent.type == "auto-reply-sent").count() == 1
    db.close()


def test_catalogue_context_exposes_only_customer_visible_details(tmp_path, monkeypatch):
    (tmp_path / "services.json").write_text(json.dumps([
        {
            "id": "private-duration",
            "name": "Scalp Care",
            "description": "A customer-visible description.",
            "price": 320,
            "duration": 75,
            "showDuration": False,
        },
        {
            "id": "public-duration",
            "name": "Relaxation Session",
            "description": "A calm professional session.",
            "price": 250,
            "duration": 60,
            "showDuration": True,
        },
    ]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))

    context = main.get_live_services_context("primary")

    assert "Scalp Care" in context
    assert "Price: $320" in context
    assert "Duration: 60 minutes" in context
    assert "Duration: 75 minutes" not in context


def test_secondary_account_keeps_primary_context_and_ai_state_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [{
        "source": "primary-only.txt", "type": "text", "text": "Primary-only knowledge",
    }])

    assert main.get_live_services_context("secondary") == ""
    assert main.build_business_context("Primary", account_key="secondary") == "No relevant business records found."

    db = make_db()
    now = datetime.utcnow()
    thread = Thread(
        id="secondary-thread",
        customer_phone="+61400000003",
        sms_account_key="secondary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=1,
        pending_slots='[{"secondary": true}]',
        created_at=now,
        updated_at=now,
    )
    customer = add_message(db, thread.id, "customer", "Hello", now, "secondary-customer")
    db.add(thread)
    db.commit()

    assert main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
    ) == (False, False)
    db.refresh(thread)
    assert thread.state == "auto-reply"
    assert thread.pending_slots == '[{"secondary": true}]'
    assert db.query(Message).filter(Message.thread_id == thread.id).count() == 1
    db.close()

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, Thread


def message(role, text):
    return SimpleNamespace(role=role, text=text)


def write_services(tmp_path):
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


def test_dinner_date_redirect_uses_visible_prices_and_hides_private_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)

    reply = main.booking_conversation_guard_reply(
        [message("customer", "Would you go on a dinner date with me?")],
        "Would you go on a dinner date with me?",
    )

    assert "professional and appointment-based" in reply
    assert "don't do personal dates or relationships" in reply
    assert "Scalp Care for AU$320" in reply
    assert "Relaxation Session for AU$250 (60 minutes)" in reply
    assert "75" not in reply
    assert reply.endswith("Which service would you like to book?")


def test_relationship_friendship_and_emotional_dependence_are_redirected(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)

    for request in (
        "Will you be my girlfriend and stay exclusive?",
        "Can we become friends outside work?",
        "You're the only one who understands me, I need you.",
        "Add me on WhatsApp so we can talk there.",
    ):
        reply = main.booking_conversation_guard_reply(
            [message("customer", request)], request,
        )
        assert "professional and appointment-based" in reply
        assert "Which service would you like to book?" in reply


def test_prolonged_non_booking_loop_gets_polite_configurable_close(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)
    (tmp_path / main.CONVERSATION_BOUNDARIES_FILENAME).write_text(json.dumps({
        "enabled": True,
        "maxNonBookingCustomerTurns": 2,
    }), encoding="utf-8")
    history = [
        message("customer", "How has your day been?"),
        message("system", "Pretty good, thanks."),
        message("customer", "What music do you like?"),
        message("agent", "A bit of everything."),
        message("customer", "Tell me something else about yourself."),
    ]

    reply = main.booking_conversation_guard_reply(
        history, history[-1].text,
    )

    assert main.consecutive_non_booking_customer_turns(history) == 3
    assert reply.startswith("Lovely chatting, but I need to keep this line focused on bookings.")
    assert reply.endswith("Which one suits you?")


def test_booking_turn_breaks_the_social_loop_and_greeting_is_not_over_pushed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)
    history = [
        message("customer", "How are you?"),
        message("system", "Good thanks."),
        message("customer", "What music do you like?"),
        message("system", "Lots of things."),
        message("customer", "How much is the Scalp Care service?"),
    ]

    assert main.consecutive_non_booking_customer_turns(history) == 0
    assert main.booking_conversation_guard_reply(history, history[-1].text) is None
    assert main.booking_conversation_guard_reply(
        [message("customer", "Hi")], "Hi",
    ) is None
    assert main.booking_conversation_guard_reply(
        [message("customer", "What date is my booking, and can my partner attend?")],
        "What date is my booking, and can my partner attend?",
    ) is None


def test_catalogue_context_exposes_only_customer_visible_details(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)

    context = main.get_live_services_context("primary")

    assert "Scalp Care" in context
    assert "Price: $320" in context
    assert "Duration: 60 minutes" in context
    assert "Duration: 75 minutes" not in context


def test_relationship_guard_runs_before_model_and_sends_short_booking_guidance(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="relationship-thread",
        customer_phone="+61400000001",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=1,
        created_at=now,
        updated_at=now,
    )
    customer = Message(
        id="relationship-message",
        thread_id=thread.id,
        role="customer",
        text="Can I take you on a dinner date?",
        provider_message_id="provider-relationship",
        at=now,
    )
    db.add_all([thread, customer])
    db.commit()
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        main,
        "openai_client",
        SimpleNamespace(responses=SimpleNamespace(create=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("relationship boundary must not depend on the model")
        ))),
    )
    sent = []
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda phone, text, **kwargs: sent.append((phone, text, kwargs)) or {"status": "success"},
    )
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)

    main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
    )

    assert len(sent) == 1
    assert "professional and appointment-based" in sent[0][1]
    assert "Scalp Care for AU$320" in sent[0][1]
    assert "75" not in sent[0][1]
    db.close()


def test_secondary_account_cannot_receive_primary_prompt_knowledge_services_or_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [{
        "source": "primary-only.txt", "type": "text", "text": "Tori-only knowledge",
    }])

    assert main.get_live_services_context("secondary") == ""
    assert main.build_business_context("Tori", account_key="secondary") == "No relevant business records found."
    assert main.booking_conversation_guard_reply(
        [message("customer", "Be my girlfriend")],
        "Be my girlfriend",
        account_key="secondary",
    ) is None

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="anonymous-thread",
        customer_phone="+61400000002",
        sms_account_key="secondary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=1,
        pending_slots='[{"secondary": true}]',
        created_at=now,
        updated_at=now,
    )
    customer = Message(
        id="anonymous-message", thread_id=thread.id, role="customer",
        text="Hello Anonymous", provider_message_id="provider-anonymous", at=now,
    )
    db.add_all([thread, customer])
    db.commit()

    assert main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
    ) == (False, False)
    db.refresh(thread)
    assert thread.state == "auto-reply"
    assert thread.pending_slots == '[{"secondary": true}]'
    assert db.query(Message).filter(Message.thread_id == thread.id).count() == 1
    db.close()

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, CalendarEvent, Message, ReplyInput, Thread, ThreadEvent


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


class CapturingClient:
    def __init__(self, text):
        self.responses = CapturingResponses(text)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_thread_and_customer(db, text, at=None):
    at = at or datetime.utcnow()
    thread = Thread(
        id="safety-thread",
        customer_phone="+61423281468",
        state="auto-reply",
        priority="medium",
        sla_due_at=at + timedelta(hours=2),
        unread_count=1,
        created_at=at,
        updated_at=at,
    )
    customer = Message(
        id="customer-message",
        thread_id=thread.id,
        role="customer",
        text=text,
        provider_message_id="provider-customer",
        at=at,
    )
    db.add_all([thread, customer])
    db.commit()
    return thread, customer


def configure_ai_test(monkeypatch, reply):
    client = CapturingClient(reply)
    monkeypatch.setattr(main, "openai_client", client)
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_business_context", lambda _body: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    return client


def test_human_reply_cancels_older_pending_ai_work(monkeypatch):
    db = make_db()
    thread, customer = add_thread_and_customer(
        db,
        "Is 3:45 still ok",
        at=datetime.utcnow() - timedelta(minutes=1),
    )
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: {"status": "success"},
    )
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)

    main.reply_thread(
        thread.id,
        ReplyInput(agentId="operator-frank", text="Yes", clientRequestId="human-click"),
        db,
    )
    monkeypatch.setattr(
        main,
        "openai_client",
        type("ForbiddenClient", (), {
            "responses": type("ForbiddenResponses", (), {
                "create": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("AI must not run after a human reply")
                )
            })()
        })(),
    )

    result = main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    )

    assert result == (False, False)
    assert db.query(Message).filter(Message.role == "system").count() == 0
    human_event = db.query(ThreadEvent).filter(ThreadEvent.type == "human-reply-sent").one()
    assert human_event.agent_id == "operator-frank"
    assert db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-cancelled").count() == 1
    db.close()


def test_human_reply_during_model_generation_wins(monkeypatch):
    db = make_db()
    thread, customer = add_thread_and_customer(
        db,
        "Is 3:45 still ok",
        at=datetime.utcnow() - timedelta(minutes=1),
    )
    gateway_calls = []
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda phone, text, **kwargs: gateway_calls.append((phone, text, kwargs)) or {"status": "success"},
    )
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_business_context", lambda _body: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_busy_slots", lambda *_args: [])

    class ReplyWhileGenerating:
        def create(self, **_kwargs):
            main.reply_thread(
                thread.id,
                ReplyInput(agentId="operator-frank", text="Yes", clientRequestId="during-model"),
                db,
            )
            return StaticResponse("Yes, 3:45 is fine.")

    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": ReplyWhileGenerating()})())

    main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    )

    assert [text for _phone, text, _kwargs in gateway_calls] == ["Yes"]
    assert db.query(Message).filter(Message.role == "system").count() == 0
    cancelled = db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-cancelled").one()
    assert json.loads(cancelled.meta)["reason"] == "human-replied-during-generation"
    db.close()


def test_availability_starts_now_and_includes_customer_booking_context(monkeypatch):
    db = make_db()
    hobart = ZoneInfo("Australia/Hobart")
    now_local = datetime(2026, 8, 11, 15, 31, tzinfo=hobart)
    thread, customer = add_thread_and_customer(db, "Is 3:45 still ok")
    db.add(CalendarEvent(
        id="owned-booking",
        customer_phone=thread.customer_phone,
        summary="Luka - 30 minutes",
        start_time=datetime(2026, 8, 11, 15, 45),
        end_time=datetime(2026, 8, 11, 16, 15),
    ))
    db.commit()
    client = configure_ai_test(monkeypatch, "Yes, your 3:45 booking is still confirmed.")
    monkeypatch.setattr(main, "current_business_time", lambda: now_local)
    monkeypatch.setattr(main.calendar_service, "get_busy_slots", lambda *_args: [{
        "start": datetime(2026, 8, 11, 15, 45, tzinfo=hobart),
        "end": datetime(2026, 8, 11, 16, 15, tzinfo=hobart),
    }])
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda *_args, **_kwargs: {"status": "success"})
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)

    main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    )

    enriched_prompt = client.responses.calls[0]["input"][-1]["content"]
    assert "No generic appointment times are supplied here" in enriched_prompt
    assert "Option 1:" not in enriched_prompt
    assert "these bookings belong to this customer" in enriched_prompt
    assert "That exact time is already this customer's confirmed booking" in enriched_prompt
    assert db.query(Message).filter(Message.role == "system").one().text.startswith("Yes")
    db.close()


def test_contradiction_of_existing_booking_is_not_sent(monkeypatch):
    db = make_db()
    hobart = ZoneInfo("Australia/Hobart")
    now_local = datetime(2026, 8, 11, 15, 34, tzinfo=hobart)
    thread, customer = add_thread_and_customer(db, "Is 3:45 still ok")
    db.add(CalendarEvent(
        id="owned-booking",
        customer_phone=thread.customer_phone,
        summary="Luka - 30 minutes",
        start_time=datetime(2026, 8, 11, 15, 45),
        end_time=datetime(2026, 8, 11, 16, 15),
    ))
    db.commit()
    configure_ai_test(monkeypatch, "Sorry, 3:45 is no longer available. I can do 4:45.")
    monkeypatch.setattr(main, "current_business_time", lambda: now_local)
    monkeypatch.setattr(main.calendar_service, "get_busy_slots", lambda *_args: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe SMS must not be sent")),
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
    assert json.loads(failure.meta)["reason"] == "contradicts-customer-booking"
    assert db.get(Thread, thread.id).state == "needs-review"
    db.close()


def test_generic_holding_reply_is_not_sent(monkeypatch):
    db = make_db()
    thread, customer = add_thread_and_customer(db, "What level?")
    configure_ai_test(
        monkeypatch,
        "Hey, I've got your message but I can't check that properly right now. I'll get back to you shortly.",
    )
    monkeypatch.setattr(main.calendar_service, "get_busy_slots", lambda *_args: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("holding SMS must not be sent")),
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
    assert json.loads(failure.meta)["reason"] == "generic-holding-reply"
    db.close()


def test_live_handoff_signal_is_never_sent_as_sms(monkeypatch):
    db = make_db()
    thread, customer = add_thread_and_customer(db, "What level?")
    configure_ai_test(monkeypatch, "[[HANDOFF: Apartment level is not recorded]]")
    monkeypatch.setattr(main.calendar_service, "get_busy_slots", lambda *_args: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("handoff marker must not be sent")),
    )

    main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    )

    assert db.query(Message).filter(Message.role != "customer").count() == 0
    request = db.query(ThreadEvent).filter(ThreadEvent.type == "information-request").one()
    assert json.loads(request.meta)["status"] == "pending"
    assert db.get(Thread, thread.id).state == "needs-review"
    db.close()

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, Thread


class Response:
    output = []

    def __init__(self, text):
        self.output_text = text


class CapturingClient:
    def __init__(self, text):
        self.responses = self
        self.pending = list(text) if isinstance(text, list) else [text]
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return Response(self.pending.pop(0))


def make_conversation(messages):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    first_at = messages[0].at
    thread = Thread(
        id="timestamp-thread",
        customer_phone="+61400000001",
        state="auto-reply",
        priority="medium",
        sla_due_at=first_at + timedelta(hours=2),
        unread_count=1,
        created_at=first_at,
        updated_at=messages[-1].at,
    )
    for message in messages:
        message.thread_id = thread.id
    db.add_all([thread, *messages])
    db.commit()
    return db, thread


def configure(monkeypatch, now_local, reply):
    client = CapturingClient(reply)
    monkeypatch.setattr(main, "current_business_time", lambda: now_local)
    monkeypatch.setattr(main, "openai_client", client)
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_business_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])
    return client


def customer(message_id, text, at):
    return Message(
        id=message_id,
        role="customer",
        text=text,
        provider_message_id=f"provider-{message_id}",
        at=at,
    )


def test_delayed_reply_to_elapsed_requested_time_uses_missed_message_language(monkeypatch):
    hobart = ZoneInfo("Australia/Hobart")
    received_utc = datetime(2026, 8, 13, 3, 0)  # 1:00 PM AEST
    newest = customer("delayed", "Can I book for 2pm today?", received_utc)
    db, thread = make_conversation([newest])
    client = configure(
        monkeypatch,
        datetime(2026, 8, 13, 16, 0, tzinfo=hobart),
        [
            "Yep, 2pm works.",
            "Sorry I missed your 2pm message. Are you after another time today?",
        ],
    )

    main.run_sms_reply_logic(
        db, thread.id, newest.text, newest.provider_message_id, newest.at, dispatch_sms=False,
    )

    reply = db.query(Message).filter(Message.role == "system").one()
    assert reply.text == "Sorry I missed your 2pm message. Are you after another time today?"
    assert "Delayed-message correction" in client.calls[0]["instructions"]
    assert "Do not book or accept that time" in client.calls[0]["instructions"]
    assert "Correction: the original requested time has passed" in client.calls[1]["instructions"]
    active_prompt = client.calls[0]["input"][-1]["content"]
    assert "[Received: Thursday 13 August 2026, 01:00 PM AEST]" in active_prompt
    assert "[Processing now: Thursday 13 August 2026, 04:00 PM AEST]" in active_prompt
    db.close()


def test_same_day_future_request_keeps_normal_scheduling_path(monkeypatch):
    hobart = ZoneInfo("Australia/Hobart")
    received_utc = datetime(2026, 8, 13, 3, 0)  # 1:00 PM AEST
    newest = customer("future", "Can I book for 2pm today?", received_utc)
    db, thread = make_conversation([newest])
    client = configure(
        monkeypatch,
        datetime(2026, 8, 13, 13, 30, tzinfo=hobart),
        "What service were you after for 2pm?",
    )

    main.run_sms_reply_logic(
        db, thread.id, newest.text, newest.provider_message_id, newest.at, dispatch_sms=False,
    )

    assert db.query(Message).filter(Message.role == "system").one().text.startswith("What service")
    assert "Delayed-message correction" not in client.calls[0]["instructions"]
    assert client.calls[0]["tool_choice"] == "required"
    db.close()


def test_model_receives_timestamped_chronological_context_and_combined_inbound_turn(monkeypatch):
    hobart = ZoneInfo("Australia/Hobart")
    history_customer = customer("history-customer", "Hello", datetime(2026, 8, 12, 22, 0))
    history_reply = Message(
        id="history-reply",
        role="system",
        text="Hey",
        at=datetime(2026, 8, 12, 22, 1),
    )
    first_fragment = customer("first-fragment", "Tomorrow", datetime(2026, 8, 13, 3, 0))
    newest = customer("newest-fragment", "At 3pm", datetime(2026, 8, 13, 3, 2))
    db, thread = make_conversation([
        history_customer, history_reply, first_fragment, newest,
    ])
    client = configure(
        monkeypatch,
        datetime(2026, 8, 13, 13, 5, tzinfo=hobart),
        "What service were you after?",
    )

    main.run_sms_reply_logic(
        db, thread.id, newest.text, newest.provider_message_id, newest.at, dispatch_sms=False,
    )

    db.refresh(thread)
    reply = db.query(Message).filter(Message.text.like("What service%")).one().text
    pending = json.loads(thread.pending_booking)
    assert reply.startswith("What service")
    assert pending["state"] == "awaiting_service"
    assert pending["requested_slot"] == "2026-08-14T15:00:00+10:00"
    assert client.calls == []
    db.close()

import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, Thread, ThreadEvent, run_sms_reply_logic


class FailingResponses:
    def create(self, **_kwargs):
        raise RuntimeError("provider failure")


class FailingOpenAIClient:
    responses = FailingResponses()


def test_openai_failure_creates_no_reply_or_draft(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="fail-closed-thread",
        customer_phone="+61412345678",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
    )
    customer = Message(
        id="fail-closed-customer",
        thread_id=thread.id,
        role="customer",
        text="Are you available tomorrow?",
        at=now,
    )
    db.add_all([thread, customer])
    db.commit()

    monkeypatch.setattr(main, "openai_client", FailingOpenAIClient())
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_business_context", lambda _body: "")
    monkeypatch.setattr(main.calendar_service, "get_busy_slots", lambda *_args: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("SMS must not be sent")),
    )

    booking_confirmed, slots_presented = run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        "provider-message",
        now,
        dispatch_sms=True,
    )

    db.refresh(thread)
    generated = db.query(Message).filter(Message.role != "customer").all()
    failure = db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-failed").one()

    assert booking_confirmed is False
    assert slots_presented is False
    assert generated == []
    assert thread.state == "needs-review"
    assert thread.pending_slots is None
    assert json.loads(failure.meta)["message_id"] == customer.id
    db.close()


def test_production_code_contains_no_canned_ai_failure_reply():
    source = Path(__file__).with_name("main.py").read_text(encoding="utf-8")

    assert "Falling back to simulation" not in source
    assert "I've got your message but I can't check that properly right now" not in source

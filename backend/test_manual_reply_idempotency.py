from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, ReplyInput, Thread


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_thread(db, thread_id="manual-thread"):
    now = datetime.utcnow()
    thread = Thread(
        id=thread_id,
        customer_phone="+61412345678",
        state="taken-over",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.commit()
    return thread


def stub_gateway(monkeypatch):
    calls = []

    def send_sms(phone, text, idempotency_key=None):
        calls.append((phone, text, idempotency_key))
        return {}

    monkeypatch.setattr(main.mobilemessage_service, "send_sms", send_sms)
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda result: None)
    return calls


def test_manual_reply_blocks_same_text_from_repeated_ui_requests(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    calls = stub_gateway(monkeypatch)

    first = main.reply_thread(
        thread.id,
        ReplyInput(agentId="tester", text="Yes, what time suits?", clientRequestId="click-1"),
        db,
    )
    repeated = main.reply_thread(
        thread.id,
        ReplyInput(agentId="tester", text="  yes,  what time suits?  ", clientRequestId="click-2"),
        db,
    )

    assert len(calls) == 1
    assert db.query(Message).filter(Message.role == "agent").count() == 1
    assert repeated["id"] == first["id"]
    assert repeated["duplicate"] is True
    db.close()


def test_manual_reply_reuses_same_client_request_id(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    calls = stub_gateway(monkeypatch)
    payload = ReplyInput(agentId="tester", text="Hello", clientRequestId="same-click")

    first = main.reply_thread(thread.id, payload, db)
    repeated = main.reply_thread(thread.id, payload, db)

    assert len(calls) == 1
    assert repeated["id"] == first["id"]
    assert repeated["duplicate"] is True
    db.close()


def test_draft_approval_only_dispatches_once(monkeypatch):
    db = make_db()
    thread = add_thread(db, "draft-thread")
    calls = stub_gateway(monkeypatch)
    draft = Message(
        id="draft-message",
        thread_id=thread.id,
        role="draft",
        text="Draft reply",
        at=datetime.utcnow(),
    )
    db.add(draft)
    db.commit()

    first = main.approve_draft_message(draft.id, db)
    repeated = main.approve_draft_message(draft.id, db)

    assert len(calls) == 1
    assert first["duplicate"] is False
    assert repeated["duplicate"] is True
    assert db.query(Message).filter(Message.id == draft.id).one().role == "agent"
    db.close()

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, ReplyInput, Thread, get_thread_detail, get_threads, reply_thread


def make_shared_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    now = datetime.utcnow()
    thread = Thread(
        id="shared-thread",
        customer_phone="+61412345678",
        state="taken-over",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
    )
    db.add_all([
        thread,
        Message(
            id="customer-message",
            thread_id=thread.id,
            role="customer",
            text="Hello",
            at=now,
        ),
    ])
    db.commit()
    return engine, session_factory, db, thread


def test_reply_from_one_session_is_visible_to_another(monkeypatch):
    _engine, session_factory, sender_db, thread = make_shared_db()
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda *_args, **_kwargs: {"status": "success"})
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)

    sent = reply_thread(
        thread.id,
        ReplyInput(agentId="person-one", text="Shared reply", clientRequestId="request-one"),
        sender_db,
    )

    viewer_db = session_factory()
    detail = get_thread_detail(thread.id, viewer_db)

    assert sent["text"] == "Shared reply"
    assert detail["messages"][-1]["text"] == "Shared reply"
    assert detail["messages"][-1]["role"] == "agent"
    viewer_db.close()
    sender_db.close()


def test_thread_list_uses_bounded_query_count():
    engine, _session_factory, db, _thread = make_shared_db()
    statements = []

    def count_statement(*_args):
        statements.append(1)

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        result = get_threads(None, None, None, None, db)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert result[0]["lastMessageText"] == "Hello"
    assert len(statements) <= 3
    db.close()


def test_api_responses_are_not_cacheable():
    response = TestClient(main.app).get("/api/health")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import (
    Base,
    InformationRequestResponseInput,
    Message,
    Thread,
    ThreadEvent,
    respond_to_information_request,
)


def test_information_request_saves_knowledge_sends_reply_and_resolves(monkeypatch, tmp_path):
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="thread-1",
        customer_phone="+61412345678",
        state="needs-review",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
        created_at=now,
        updated_at=now,
    )
    customer_message = Message(
        id="customer-1",
        thread_id=thread.id,
        role="customer",
        text="Do you offer the couples service?",
        at=now,
    )
    request_event = ThreadEvent(
        id="request-1",
        thread_id=thread.id,
        type="information-request",
        meta=json.dumps({
            "reason": "The service list does not say whether couples are accepted.",
            "status": "pending",
            "customer_message_id": customer_message.id,
        }),
        at=now,
    )
    db.add_all([thread, customer_message, request_event])
    db.commit()

    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "generate_information_request_content", lambda *_args: {
        "customer_reply": "Yes, couples are welcome. What day were you thinking?",
        "knowledge_summary": "Couples are accepted for the couples service.",
    })
    sent = []
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda phone, text, idempotency_key: sent.append((phone, text)) or {})
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)

    result = respond_to_information_request(
        thread.id,
        InformationRequestResponseInput(
            agentId="owner",
            information="Yes, couples are accepted for that service.",
            requestEventId=request_event.id,
        ),
        db,
    )

    db.refresh(thread)
    db.refresh(request_event)
    request_meta = json.loads(request_event.meta)
    knowledge_lines = (tmp_path / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8").splitlines()
    knowledge_entry = json.loads(knowledge_lines[0])

    assert result["status"] == "success"
    assert sent == [(thread.customer_phone, "Yes, couples are welcome. What day were you thinking?")]
    assert thread.state == "auto-reply"
    assert thread.unread_count == 0
    assert request_meta["status"] == "resolved"
    assert knowledge_entry["id"] == request_event.id
    assert knowledge_entry["text"] == "Couples are accepted for the couples service."
    assert db.query(Message).filter(Message.role == "system").one().text == result["message"]["text"]
    assert db.query(ThreadEvent).filter(ThreadEvent.type == "information-request-resolved").count() == 1
    db.close()


def test_old_handoff_event_is_treated_as_an_information_request():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    db.add(Thread(
        id="thread-old",
        customer_phone="+61400000000",
        state="needs-review",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
        created_at=now,
        updated_at=now,
    ))
    db.add(ThreadEvent(
        id="old-handoff",
        thread_id="thread-old",
        type="catch-up-handoff",
        meta=json.dumps({"reason": "Missing price"}),
        at=now,
    ))
    db.commit()

    pending = main.find_pending_information_request(db, "thread-old")

    assert pending is not None
    assert pending.id == "old-handoff"
    db.close()

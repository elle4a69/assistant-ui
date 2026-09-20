import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
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
    monkeypatch.setattr(main, "classify_knowledge_entries", lambda entries: {
        entries[0]["id"]: {
            "scope": "primary",
            "category": "service_specific",
            "retrieval_enabled": True,
            "classification_status": "classified",
            "classification_version": 1,
        },
    })
    monkeypatch.setattr(main, "generate_information_request_content", lambda *_args: {
        "customer_reply": "Yes, couples are welcome. What day were you thinking?",
        "knowledge_summary": "Couples are accepted for the couples service.",
    })
    sent = []
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda phone, text, idempotency_key, account_key="primary": sent.append((phone, text, account_key)) or {})
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
    assert sent == [(thread.customer_phone, "Yes, couples are welcome. What day were you thinking?", "primary")]
    assert thread.state == "auto-reply"
    assert thread.unread_count == 0
    assert request_meta["status"] == "resolved"
    assert knowledge_entry["id"] == request_event.id
    assert knowledge_entry["text"] == "Yes, couples are accepted for that service."
    assert knowledge_entry["status"] == "active"
    assert knowledge_entry["review_status"] == "approved"
    assert knowledge_entry["retrieval_enabled"] is True
    assert knowledge_entry["scope"] == "primary"
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


def _information_request_db(*, account_key="secondary"):
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="guarded-thread",
        customer_phone="+61400000000",
        sms_account_key=account_key,
        state="needs-review",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
        created_at=now,
        updated_at=now,
    )
    customer = Message(
        id="guarded-customer",
        thread_id=thread.id,
        role="customer",
        text="Do you offer this service?",
        provider_message_id="provider-1",
        at=now,
    )
    request = ThreadEvent(
        id="guarded-request",
        thread_id=thread.id,
        type="information-request",
        meta=json.dumps({"status": "pending", "customer_message_id": customer.id}),
        at=now,
    )
    db.add_all([thread, customer, request])
    db.commit()
    payload = InformationRequestResponseInput(
        agentId="owner",
        information="This service is offered on this line.",
        requestEventId=request.id,
    )
    return db, thread, customer, request, payload


def test_unavailable_generation_saves_owner_knowledge_without_sending_sms(monkeypatch, tmp_path):
    db, thread, _customer, request, payload = _information_request_db()
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    def unavailable_after_knowledge_save(*_args):
        assert (tmp_path / main.LEARNED_INFORMATION_FILENAME).exists()
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main, "generate_information_request_content", unavailable_after_knowledge_save)
    monkeypatch.setattr(main, "classify_knowledge_entries", lambda entries: {
        entries[0]["id"]: {
            "scope": "secondary",
            "category": "service_specific",
            "retrieval_enabled": True,
        },
    })
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: pytest.fail("SMS must not be sent"),
    )

    result = respond_to_information_request(thread.id, payload, db)

    db.refresh(request)
    request_meta = json.loads(request.meta)
    knowledge_entry = json.loads((tmp_path / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8"))
    assert result["status"] == "knowledge-saved"
    assert result["replySent"] is False
    assert result["message"] is None
    assert request_meta["status"] == "knowledge-saved"
    assert request_meta["reply_unavailable_reason"] == "AI response unavailable"
    assert db.query(Message).filter(Message.role != "customer").count() == 0
    assert db.query(ThreadEvent).filter(ThreadEvent.type == "information-request-knowledge-saved").count() == 1
    assert knowledge_entry["text"] == payload.information
    assert knowledge_entry["status"] == "active"
    db.close()


def test_unavailable_generation_does_not_save_after_arrival(monkeypatch, tmp_path):
    db, thread, _customer, _request, payload = _information_request_db()
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "generate_information_request_content",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(main, "customer_arrival_has_been_recorded", lambda *_args: True)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: pytest.fail("SMS must not be sent"),
    )

    with pytest.raises(HTTPException, match="conversation changed"):
        respond_to_information_request(thread.id, payload, db)

    assert db.query(Message).filter(Message.role != "customer").count() == 0
    assert db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-cancelled").count() == 1
    assert not (tmp_path / main.LEARNED_INFORMATION_FILENAME).exists()
    db.close()


def test_training_mode_creates_draft_without_sms(monkeypatch, tmp_path):
    db, thread, _customer, _request, payload = _information_request_db()
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", True)
    monkeypatch.setattr(main, "generate_information_request_content", lambda *_args: {
        "customer_reply": "Yes, we offer this service.",
        "knowledge_summary": "This service is offered.",
    })
    monkeypatch.setattr(main, "classify_knowledge_entries", lambda entries: {
        entries[0]["id"]: {
            "scope": "secondary",
            "category": "service_specific",
            "retrieval_enabled": True,
        },
    })
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: pytest.fail("training mode must not send SMS"),
    )

    result = respond_to_information_request(thread.id, payload, db)

    db.refresh(thread)
    assert result["message"]["role"] == "draft"
    assert thread.state == "needs-review"
    assert db.query(Message).filter(Message.role == "draft").count() == 1
    assert db.query(ThreadEvent).filter(ThreadEvent.type == "draft-created").count() == 1
    db.close()


@pytest.mark.parametrize("guard", ["availability", "superseded", "arrival"])
def test_response_validation_guards_never_send_sms_and_only_save_current_owner_knowledge(monkeypatch, tmp_path, guard):
    db, thread, customer, request, payload = _information_request_db()
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    reply = "Yes, it is available tomorrow at 2pm." if guard == "availability" else "Yes, we offer it."
    monkeypatch.setattr(main, "generate_information_request_content", lambda *_args: {
        "customer_reply": reply,
        "knowledge_summary": "This service is offered.",
    })
    monkeypatch.setattr(main, "customer_arrival_has_been_recorded", lambda *_args: guard == "arrival")
    if guard == "superseded":
        db.add(Message(
            id="newer-customer",
            thread_id=thread.id,
            role="customer",
            text="Never mind, a different question.",
            provider_message_id="provider-2",
            at=customer.at + timedelta(seconds=1),
        ))
        db.commit()
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: pytest.fail("SMS must not be sent"),
    )

    with pytest.raises(HTTPException, match="conversation changed|did not pass current conversation safeguards"):
        respond_to_information_request(thread.id, payload, db)

    db.refresh(request)
    assert json.loads(request.meta)["status"] == "pending"
    assert db.query(Message).filter(Message.role == "system").count() == 0
    expected_event = "ai-reply-failed" if guard == "availability" else "ai-reply-cancelled"
    assert db.query(ThreadEvent).filter(ThreadEvent.type == expected_event).count() == 1
    knowledge_path = tmp_path / main.LEARNED_INFORMATION_FILENAME
    if guard == "availability":
        assert knowledge_path.exists()
        assert json.loads(knowledge_path.read_text(encoding="utf-8"))["text"] == payload.information
    else:
        assert not knowledge_path.exists()
    db.close()

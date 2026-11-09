"""Tests for iPhone Shortcuts webhook (/weebhook and /webhook)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.core.database
import backend.main
import backend.services.sms_service
from backend.core.database import Base, get_db
from backend.main import app
from backend.models.domain import Message, Thread, OutboundQueueMessage


@pytest.fixture
def test_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(backend.core.database, "SessionLocal", TestingSession)
    monkeypatch.setattr(backend.services.sms_service, "SessionLocal", TestingSession)
    monkeypatch.setattr(backend.main, "SessionLocal", TestingSession, raising=False)
    monkeypatch.setattr(backend.services.sms_service, "AUTO_REPLY_GLOBAL_ENABLED", False)
    monkeypatch.setattr(backend.main, "AUTO_REPLY_GLOBAL_ENABLED", False, raising=False)
    monkeypatch.setattr(backend.services.sms_service, "_process_sms_reply_unlocked", lambda *a, **k: None)

    yield TestingSession
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_db):
    return TestClient(app)


def test_iphone_webhook_json_sender_message(client, test_db):
    payload = {
        "sender": "+61412345678",
        "message": "Hi, are you free tomorrow?",
    }
    response = client.post("/weebhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["thread_id"] is not None

    with test_db() as db:
        thread = db.query(Thread).filter(Thread.id == data["thread_id"]).first()
        assert thread is not None
        assert thread.customer_phone == "+61412345678"

        msg = db.query(Message).filter(Message.thread_id == thread.id).first()
        assert msg is not None
        assert msg.text == "Hi, are you free tomorrow?"
        assert msg.role == "customer"


def test_iphone_webhook_json_from_text_canonicalization(client, test_db):
    payload = {
        "from": "0412345678",
        "text": "Test message",
    }
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["thread_id"] is not None

    with test_db() as db:
        thread = db.query(Thread).filter(Thread.id == data["thread_id"]).first()
        assert thread is not None
        # 0412345678 canonicalized to +61412345678
        assert thread.customer_phone == "+61412345678"

        msg = db.query(Message).filter(Message.thread_id == thread.id).first()
        assert msg is not None
        assert msg.text == "Test message"


def test_iphone_webhook_form_urlencoded(client, test_db):
    form_data = {
        "phone": "0498765432",
        "body": "Form encoded test",
    }
    response = client.post(
        "/weebhook",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    with test_db() as db:
        thread = db.query(Thread).filter(Thread.id == data["thread_id"]).first()
        assert thread is not None
        assert thread.customer_phone == "+61498765432"

        msg = db.query(Message).filter(Message.thread_id == thread.id).first()
        assert msg is not None
        assert msg.text == "Form encoded test"


def test_iphone_webhook_alternative_field_names(client, test_db):
    # contact + msg
    res1 = client.post("/webhook", json={"contact": "+61400111222", "msg": "Variant 1"})
    assert res1.status_code == 200
    assert res1.json()["status"] == "success"

    # source + content
    res2 = client.post("/weebhook", json={"source": "+61400111333", "content": "Variant 2"})
    assert res2.status_code == 200
    assert res2.json()["status"] == "success"

    # from_phone + text
    res3 = client.post("/webhook", json={"from_phone": "+61400111444", "text": "Variant 3"})
    assert res3.status_code == 200
    assert res3.json()["status"] == "success"


def test_iphone_webhook_inbound_endpoint(client, test_db):
    payload = {
        "sender": "+61412345678",
        "message": "Testing webhook inbound endpoint",
    }
    response = client.post("/webhook/inbound", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["thread_id"] is not None

    with test_db() as db:
        thread = db.query(Thread).filter(Thread.id == data["thread_id"]).first()
        assert thread is not None
        assert thread.customer_phone == "+61412345678"

        msg = db.query(Message).filter(Message.thread_id == thread.id).first()
        assert msg is not None
        assert msg.text == "Testing webhook inbound endpoint"

    # Also test /weebhook/inbound alias
    res_alias = client.post("/weebhook/inbound", json={"from": "0412345678", "text": "Alias inbound test"})
    assert res_alias.status_code == 200
    assert res_alias.json()["status"] == "success"


def test_iphone_webhook_inbound_get_info(client):
    for path in ("/webhook/inbound", "/weebhook/inbound", "/webhook", "/weebhook"):
        res = client.get(path)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["endpoint"] == "inbound_sms_webhook"
        assert "POST" in data["supported_methods"]
        assert "Inbound SMS webhook is active" in data["message"]


def test_iphone_webhook_missing_fields(client):
    # Missing sender
    res1 = client.post("/weebhook", json={"message": "No sender"})
    assert res1.status_code == 422

    # Missing body
    res2 = client.post("/weebhook", json={"sender": "+61412345678"})
    assert res2.status_code == 422

    # Empty sender
    res3 = client.post("/webhook", json={"sender": "   ", "message": "Valid body"})
    assert res3.status_code == 422

    # Empty body
    res4 = client.post("/webhook", json={"sender": "+61412345678", "message": "   "})
    assert res4.status_code == 422


def test_iphone_webhook_outbound_empty(client):
    # GET /webhook/outbound with no pending messages
    res_get = client.get("/webhook/outbound")
    assert res_get.status_code == 200
    assert res_get.json() == {"has_message": "no"}

    # POST /webhook/outbound with no payload (polling for messages)
    res_post = client.post("/webhook/outbound", json={})
    assert res_post.status_code == 200
    assert res_post.json() == {"has_message": "no"}


def test_iphone_webhook_outbound_enqueue_and_dequeue(client, test_db):
    # Enqueue a message
    enqueue_res = client.post(
        "/webhook/outbound",
        json={"phone": "+61412345678", "message": "Your booking is confirmed!"},
    )
    assert enqueue_res.status_code == 200
    enqueue_data = enqueue_res.json()
    assert enqueue_data["status"] == "queued"
    assert enqueue_data["id"] is not None

    with test_db() as db:
        item = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id == enqueue_data["id"]).first()
        assert item is not None
        assert item.phone == "+61412345678"
        assert item.message == "Your booking is confirmed!"
        assert item.status == "pending"

    # Poll via GET to dequeue
    dequeue_res = client.get("/webhook/outbound")
    assert dequeue_res.status_code == 200
    dequeue_data = dequeue_res.json()
    assert dequeue_data["has_message"] == "yes"
    assert dequeue_data["phone"] == "+61412345678"
    assert dequeue_data["reply"] == "Your booking is confirmed!"
    assert dequeue_data["message"] == "Your booking is confirmed!"

    with test_db() as db:
        item = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id == enqueue_data["id"]).first()
        assert item.status == "dispatched"
        assert item.dispatched_at is not None

    # Next check has no messages
    next_check = client.get("/webhook/outbound")
    assert next_check.status_code == 200
    assert next_check.json() == {"has_message": "no"}


def test_iphone_webhook_outbound_weebhook_alias(client):
    # Enqueue via alias
    enqueue_res = client.post(
        "/weebhook/outbound",
        json={"to": "0412345678", "body": "Alias queue test"},
    )
    assert enqueue_res.status_code == 200
    assert enqueue_res.json()["status"] == "queued"

    # Dequeue via alias POST without body
    dequeue_res = client.post("/weebhook/outbound")
    assert dequeue_res.status_code == 200
    data = dequeue_res.json()
    assert data["has_message"] == "yes"
    # Canonicalized
    assert data["phone"] == "+61412345678"
    assert data["reply"] == "Alias queue test"


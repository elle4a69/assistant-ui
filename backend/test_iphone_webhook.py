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
    monkeypatch.setattr("backend.services.relay_service.SessionLocal", TestingSession, raising=False)
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
    assert res_get.json() == {"has_message": "no", "messages": []}

    # POST /webhook/outbound with no payload (polling for messages)
    res_post = client.post("/webhook/outbound", json={})
    assert res_post.status_code == 200
    assert res_post.json() == {"has_message": "no", "messages": []}


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
    assert next_check.json() == {"has_message": "no", "messages": []}


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



def test_relay_sms_pending_batch_retrieval(client, test_db):
    # Enqueue two messages
    res1 = client.post("/webhook/outbound", json={"phone": "+61411111111", "message": "Batch message 1"})
    assert res1.status_code == 200
    id1 = res1.json()["id"]

    res2 = client.post("/webhook/outbound", json={"phone": "+61422222222", "message": "Batch message 2"})
    assert res2.status_code == 200
    id2 = res2.json()["id"]

    # Poll via /api/v1/relay/sms/pending
    pending_res = client.get("/api/v1/relay/sms/pending")
    assert pending_res.status_code == 200
    data = pending_res.json()

    assert data["has_message"] == "yes"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["id"] == id1
    assert data["messages"][0]["phone"] == "+61411111111"
    assert data["messages"][0]["reply"] == "Batch message 1"
    assert data["messages"][1]["id"] == id2
    assert data["messages"][1]["phone"] == "+61422222222"
    assert data["messages"][1]["reply"] == "Batch message 2"

    # Backwards-compatible top-level keys match first message
    assert data["id"] == id1
    assert data["phone"] == "+61411111111"
    assert data["reply"] == "Batch message 1"
    assert data["message"] == "Batch message 1"

    # Both marked dispatched in DB
    with test_db() as db:
        items = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id.in_([id1, id2])).all()
        assert len(items) == 2
        assert all(it.status == "dispatched" for it in items)

    # Next check has no messages
    empty_res = client.get("/api/v1/relay/sms/pending")
    assert empty_res.status_code == 200
    assert empty_res.json() == {"has_message": "no", "messages": []}


def test_relay_sms_sent_delivery_receipt(client, test_db):
    # Enqueue two messages
    r1 = client.post("/webhook/outbound", json={"phone": "+61433333333", "message": "Receipt test 1"})
    r2 = client.post("/webhook/outbound", json={"phone": "+61444444444", "message": "Receipt test 2"})
    id1 = r1.json()["id"]
    id2 = r2.json()["id"]

    # Test GET healthcheck
    for path in ("/webhook/sent", "/weebhook/sent", "/api/v1/relay/sms/sent"):
        res = client.get(path)
        assert res.status_code == 200
        assert res.json() == {"status": "ok", "endpoint": "delivery_receipt", "supported_methods": ["POST"]}

    # Deliver id1 via JSON list
    deliv_res1 = client.post("/webhook/sent", json={"ids": [id1]})
    assert deliv_res1.status_code == 200
    assert deliv_res1.json() == {"status": "success", "delivered_count": 1, "ids": [id1]}

    # Deliver id2 via form-urlencoded message_id
    deliv_res2 = client.post(
        "/api/v1/relay/sms/sent",
        data={"message_id": id2},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert deliv_res2.status_code == 200
    assert deliv_res2.json() == {"status": "success", "delivered_count": 1, "ids": [id2]}

    # Verify both are marked delivered with timestamp
    with test_db() as db:
        items = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id.in_([id1, id2])).all()
        assert len(items) == 2
        for it in items:
            assert it.status == "delivered"
            assert it.delivered_at is not None


def test_gateway_wakeup_debouncing_and_stranded_worker(test_db, monkeypatch):
    import backend.services.relay_service as rs
    from datetime import datetime, timedelta

    pings = []
    monkeypatch.setattr(rs, "_fire_http_ping", lambda url: pings.append(url))
    monkeypatch.setenv("ANDROID_GATEWAY_WAKEUP_URL", "https://example.com/wakeup")
    rs.last_wakeup_sent_at = None

    # First call: triggers
    assert rs.trigger_gateway_wakeup(force=False) is True
    assert len(pings) == 1

    # Second immediate call (within 10s): debounced
    assert rs.trigger_gateway_wakeup(force=False) is False
    assert len(pings) == 1

    # Force bypasses debounce
    assert rs.trigger_gateway_wakeup(force=True) is True
    assert len(pings) == 2

    # Simulate stranded message older than 15s
    with test_db() as db:
        stranded_msg = OutboundQueueMessage(
            id="stranded-1",
            phone="+61499999999",
            message="Old stranded message",
            status="pending",
            created_at=datetime.utcnow() - timedelta(seconds=25),
        )
        db.add(stranded_msg)
        db.commit()

    # Stranded check finds the message and forces wakeup
    cutoff = datetime.utcnow() - timedelta(seconds=15)
    with test_db() as db:
        count = (
            db.query(OutboundQueueMessage)
            .filter(
                OutboundQueueMessage.status == "pending",
                OutboundQueueMessage.created_at <= cutoff,
            )
            .count()
        )
        assert count >= 1


def test_relay_sms_mms_image_url_enqueue_and_dequeue(client, test_db):
    payload = {
        "phone": "+61400111222",
        "reply": "Here is our price list and availability for this week!",
        "image_url": "https://your-api.com/pricelist.png",
    }
    enqueue_res = client.post("/webhook/outbound", json=payload)
    assert enqueue_res.status_code == 200
    msg_id = enqueue_res.json()["id"]

    # Verify DB has image_url stored
    with test_db() as db:
        item = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id == msg_id).first()
        assert item is not None
        assert item.phone == "+61400111222"
        assert item.message == "Here is our price list and availability for this week!"
        assert item.image_url == "https://your-api.com/pricelist.png"

    # Dequeue via /api/v1/relay/sms/pending
    pending_res = client.get("/api/v1/relay/sms/pending")
    assert pending_res.status_code == 200
    data = pending_res.json()
    assert data["has_message"] == "yes"
    assert data["id"] == msg_id
    assert data["image_url"] == "https://your-api.com/pricelist.png"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["id"] == msg_id
    assert data["messages"][0]["image_url"] == "https://your-api.com/pricelist.png"

def test_fire_http_ping_embedded_credentials_and_message_endpoint(monkeypatch):
    import base64
    import json
    import urllib.request
    from backend.services.relay_service import _fire_http_ping

    captured_requests = []

    class DummyResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    def dummy_urlopen(req, timeout=5):
        captured_requests.append(req)
        return DummyResponse()

    monkeypatch.setattr(urllib.request, "urlopen", dummy_urlopen)
    monkeypatch.delenv("ANDROID_GATEWAY_WAKEUP_USER", raising=False)
    monkeypatch.delenv("ANDROID_GATEWAY_WAKEUP_PASSWORD", raising=False)
    monkeypatch.delenv("ANDROID_GATEWAY_WAKEUP_PHONE", raising=False)
    monkeypatch.delenv("ANDROID_GATEWAY_WAKEUP_MESSAGE", raising=False)

    # Test 1: URL with embedded credentials and /message path
    _fire_http_ping("https://smsuser:secret99@gateway.local:8080/message")
    assert len(captured_requests) == 1
    req1 = captured_requests[0]
    assert req1.full_url == "https://gateway.local:8080/message"
    assert req1.get_method() == "POST"
    expected_token = base64.b64encode(b"smsuser:secret99").decode("ascii")
    assert req1.headers.get("Authorization") == f"Basic {expected_token}"
    body1 = json.loads(req1.data.decode("utf-8"))
    assert body1["message"] == "DOORBELL PING from Android Gateway"
    assert body1["phoneNumbers"] == ["+61432172148"]

    # Test 2: URL without credentials, env vars supplied, custom phone and message
    monkeypatch.setenv("ANDROID_GATEWAY_WAKEUP_USER", "envuser")
    monkeypatch.setenv("ANDROID_GATEWAY_WAKEUP_PASSWORD", "envpass")
    monkeypatch.setenv("ANDROID_GATEWAY_WAKEUP_PHONE", "+61411222333")
    monkeypatch.setenv("ANDROID_GATEWAY_WAKEUP_MESSAGE", "Wakeup Test")

    _fire_http_ping("https://ping.local/ping")
    assert len(captured_requests) == 2
    req2 = captured_requests[1]
    assert req2.full_url == "https://ping.local/ping"
    expected_token2 = base64.b64encode(b"envuser:envpass").decode("ascii")
    assert req2.headers.get("Authorization") == f"Basic {expected_token2}"
    body2 = json.loads(req2.data.decode("utf-8"))
    assert body2["message"] == "Wakeup Test"
    assert body2["phoneNumbers"] == ["+61411222333"]

    # Test 3: Fallback when not message path and no phone
    monkeypatch.delenv("ANDROID_GATEWAY_WAKEUP_PHONE", raising=False)
    monkeypatch.delenv("ANDROID_GATEWAY_WAKEUP_USER", raising=False)
    monkeypatch.delenv("ANDROID_GATEWAY_WAKEUP_PASSWORD", raising=False)
    _fire_http_ping("https://ping.local/doorbell")
    assert len(captured_requests) == 3
    req3 = captured_requests[2]
    assert req3.full_url == "https://ping.local/doorbell"
    assert "Authorization" not in req3.headers
    assert req3.data == b"{}"


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
    assert body1["phoneNumbers"] == ["+61436281573"]

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

def test_android_sms_gateway_webhook(client):
    # 1. sms:received event with nested payload
    payload = {
        "deviceId": "ffffffff-1234-5678-abcd-000000000000",
        "event": "sms:received",
        "payload": {
            "message": "Android gateway test",
            "sender": "+61499888777",
            "recipient": "+61400000000",
            "receivedAt": "2026-11-09T22:20:00Z"
        }
    }
    res = client.post("/webhook/inbound", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "thread_id" in data

    # 2. Non-message event like sms:sent or sms:delivered
    res_sent = client.post("/webhook/inbound", json={"event": "sms:sent", "deviceId": "ffffffff..."})
    assert res_sent.status_code == 200
    data_sent = res_sent.json()
    assert data_sent["status"] == "ignored"
    assert data_sent["event"] == "sms:sent"

    res_deliv = client.post("/weebhook/inbound", json={"event": "sms:delivered", "deviceId": "ffffffff..."})
    assert res_deliv.status_code == 200
    data_deliv = res_deliv.json()
    assert data_deliv["status"] == "ignored"
    assert data_deliv["event"] == "sms:delivered"

def test_webhook_log_telemetry(client, capsys):
    # 1. GET healthcheck
    res_get = client.get("/webhook/log")
    assert res_get.status_code == 200
    assert res_get.json() == {
        "status": "ok",
        "endpoint": "shortcut_telemetry",
        "supported_methods": ["POST"],
    }

    # 2. POST with arbitrary JSON dict
    payload_json = {
        "action": "shortcut_started",
        "step": 3,
        "detail": {"input_count": 5}
    }
    res1 = client.post("/webhook/log", json=payload_json)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "logged"
    assert "timestamp" in data1
    captured1 = capsys.readouterr()
    assert "[SHORTCUT_TELEMETRY]" in captured1.out
    assert "shortcut_started" in captured1.out

    # 3. POST with arbitrary plain text string
    raw_str = "Error: Failed to fetch contact in step 4"
    res2 = client.post(
        "/webhook/log",
        content=raw_str,
        headers={"Content-Type": "text/plain"},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "logged"
    assert "timestamp" in data2
    captured2 = capsys.readouterr()
    assert "[SHORTCUT_TELEMETRY]" in captured2.out
    assert "Error: Failed to fetch contact in step 4" in captured2.out

    # 4. POST with weebhook alias
    res3 = client.post(
        "/weebhook/log",
        json={"test": "alias_log"},
    )
    assert res3.status_code == 200
    assert res3.json()["status"] == "logged"

def test_relay_route_aliases_and_shortcut_download(client, test_db, tmp_path, monkeypatch):
    import os
    import backend.routes.misc as misc

    # 1. /api/v1/relay/log (GET & POST)
    res_get_log = client.get("/api/v1/relay/log")
    assert res_get_log.status_code == 200
    assert res_get_log.json()["status"] == "ok"

    res_post_log = client.post("/api/v1/relay/log", json={"relay": "alias_test"})
    assert res_post_log.status_code == 200
    assert res_post_log.json()["status"] == "logged"

    # 2. /api/v1/relay/sms/ack (GET & POST)
    res_get_ack = client.get("/api/v1/relay/sms/ack")
    assert res_get_ack.status_code == 200
    assert res_get_ack.json()["endpoint"] == "delivery_receipt"

    # Enqueue a message to ack
    enq_res = client.post("/webhook/outbound", json={"phone": "+61412000111", "message": "Ack test"})
    assert enq_res.status_code == 200
    msg_id = enq_res.json()["id"]

    # Ack with {"token": "secret_token", "message_id": msg_id}
    ack_res = client.post("/api/v1/relay/sms/ack", json={"token": "test_tok", "message_id": msg_id})
    assert ack_res.status_code == 200
    ack_data = ack_res.json()
    assert ack_data["status"] == "success"
    assert msg_id in ack_data["ids"]

    # 3. /AI_SMS_Relay_Upgraded.shortcut (404 when missing, 200 with attachment when present)
    # When file doesn't exist
    res_missing = client.get("/AI_SMS_Relay_Upgraded.shortcut")
    assert res_missing.status_code in (404, 200) # either 404 or served if already placed

    # Create dummy shortcut file in tmp dir and monkeypatch DATA_DIR
    fake_static = tmp_path / "static"
    fake_static.mkdir(parents=True, exist_ok=True)
    fake_file = fake_static / "AI_SMS_Relay_Upgraded.shortcut"
    fake_file.write_bytes(b"SHORTCUT_BINARY_CONTENT")

    monkeypatch.setattr(misc, "DATA_DIR", str(tmp_path))

    res_download = client.get("/AI_SMS_Relay_Upgraded.shortcut")
    assert res_download.status_code == 200
    assert res_download.content == b"SHORTCUT_BINARY_CONTENT"
    assert "attachment" in res_download.headers.get("content-disposition", "")
    assert "AI_SMS_Relay_Upgraded.shortcut" in res_download.headers.get("content-disposition", "")

    # Alias /static/AI_SMS_Relay_Upgraded.shortcut
    res_download_alias = client.get("/static/AI_SMS_Relay_Upgraded.shortcut")
    assert res_download_alias.status_code == 200
    assert res_download_alias.content == b"SHORTCUT_BINARY_CONTENT"

def test_multitenant_token_outbound_queue_and_telemetry(client, test_db, capsys):
    # 1. Enqueue message for tenant_alpha
    res_a = client.post(
        "/webhook/outbound",
        json={
            "phone": "+61411111111",
            "message": "Alpha tenant message",
            "token": "tenant_alpha"
        }
    )
    assert res_a.status_code == 200
    id_a = res_a.json()["id"]
    assert res_a.json().get("client_id") == "tenant_alpha"

    # Enqueue general untokened message
    res_gen = client.post(
        "/webhook/outbound",
        json={
            "phone": "+61422222222",
            "message": "General message"
        }
    )
    assert res_gen.status_code == 200
    id_gen = res_gen.json()["id"]

    # Verify DB storage
    with test_db() as db:
        msg_a = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id == id_a).first()
        assert msg_a.client_id == "tenant_alpha"
        msg_gen = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id == id_gen).first()
        assert msg_gen.client_id is None

    # 2. Polling for tenant_beta (should have no messages)
    res_beta = client.get("/api/v1/relay/sms/pending?token=tenant_beta")
    assert res_beta.status_code == 200
    assert res_beta.json() == {"has_message": "no", "messages": []}

    # 3. Polling for tenant_alpha retrieves only tenant_alpha's message
    res_poll_a = client.get("/api/v1/relay/sms/pending?token=tenant_alpha")
    assert res_poll_a.status_code == 200
    data_a = res_poll_a.json()
    assert data_a["has_message"] == "yes"
    assert data_a["id"] == id_a
    assert data_a["client_id"] == "tenant_alpha"
    assert len(data_a["messages"]) == 1

    # 4. Polling general queue (untokened) retrieves only the untokened general message
    res_poll_gen = client.get("/webhook/outbound")
    assert res_poll_gen.status_code == 200
    data_gen = res_poll_gen.json()
    assert data_gen["has_message"] == "yes"
    assert data_gen["id"] == id_gen
    assert data_gen.get("client_id") == ""
    assert data_gen.get("image_url") == ""
    assert data_gen["messages"][0]["client_id"] == ""
    assert data_gen["messages"][0]["image_url"] == ""

    # 5. Telemetry logging with token
    # Case A: token in JSON payload
    capsys.readouterr() # clear buffer
    res_log1 = client.post("/webhook/log", json={"token": "tenant_gamma", "event": "step1"})
    assert res_log1.status_code == 200
    assert res_log1.json()["token"] == "tenant_gamma"
    captured1 = capsys.readouterr()
    assert "[SHORTCUT_TELEMETRY][token=tenant_gamma]" in captured1.out

    # Case B: token in query param with plain text
    res_log2 = client.post("/api/v1/relay/log?client_id=tenant_delta", content="Raw step text")
    assert res_log2.status_code == 200
    assert res_log2.json()["token"] == "tenant_delta"
    captured2 = capsys.readouterr()
    assert "[SHORTCUT_TELEMETRY][token=tenant_delta]" in captured2.out

    # Case C: untokened logging (backward compatibility)
    res_log3 = client.post("/webhook/log", json={"event": "untokened_step"})
    assert res_log3.status_code == 200
    assert "token" not in res_log3.json()
    captured3 = capsys.readouterr()
    assert "[SHORTCUT_TELEMETRY] " in captured3.out
    assert "[token=" not in captured3.out

    # 6. Delivery receipt / ack with token
    res_ack = client.post("/api/v1/relay/sms/ack?token=tenant_alpha", json={"message_id": id_a})
    assert res_ack.status_code == 200
    data_ack = res_ack.json()
    assert data_ack["status"] == "success"
    assert data_ack["token"] == "tenant_alpha"
    assert id_a in data_ack["ids"]

def test_iphone_webhook_sliding_window_deduplication(client, test_db, capsys):
    import time
    from backend.models.domain import Message

    payload = {
        "sender": "+61412999888",
        "message": "Hello Frank dedupe test",
        "token": "client-token-01"
    }

    # First request: successfully processed
    res1 = client.post("/webhook/inbound", json=payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "success"
    assert data1.get("duplicate") is not True
    thread_id = data1["thread_id"]
    assert thread_id is not None

    # Check database: exactly 1 customer message
    with test_db() as db:
        msgs1 = db.query(Message).filter(Message.thread_id == thread_id, Message.role == "customer").all()
        assert len(msgs1) == 1
        assert msgs1[0].text == "Hello Frank dedupe test"

    # Second identical request 1 second later from matching Personal Automation trigger
    time.sleep(1.0)
    capsys.readouterr()
    res2 = client.post("/webhook/inbound", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "success"
    assert data2["duplicate"] is True
    assert data2["thread_id"] == thread_id

    # Verify stdout log output captured
    captured = capsys.readouterr()
    assert "[iPhone Inbound Deduplicated]" in captured.out
    assert "+61412999888" in captured.out

    # Check database: still exactly 1 customer message (no duplicates created)
    with test_db() as db:
        msgs2 = db.query(Message).filter(Message.thread_id == thread_id, Message.role == "customer").all()
        assert len(msgs2) == 1

def test_unified_master_shortcut_generation_and_download(client):
    import plistlib
    import os

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    shortcut_file = os.path.join(repo_root, "AI_Booking_Relay_Master.shortcut")
    plist_file = os.path.join(repo_root, "AI_Booking_Relay_Master.plist")

    assert os.path.exists(shortcut_file), "AI_Booking_Relay_Master.shortcut does not exist"
    assert os.path.exists(plist_file), "AI_Booking_Relay_Master.plist does not exist"

    # 1. Parse binary shortcut
    with open(shortcut_file, "rb") as f:
        data = plistlib.load(f)

    assert data.get("WFWorkflowHasShortcutInputVariables") is True
    actions = data.get("WFWorkflowActions", [])
    assert len(actions) == 29, f"Expected 29 actions, got {len(actions)}"

    # Check Import Questions
    questions = data.get("WFWorkflowImportQuestions", [])
    assert len(questions) == 1
    assert questions[0]["ParameterKey"] == "WFTextActionText"
    assert "CLIENT_01" in questions[0]["DefaultValue"]

    # Verify Action 0: Text Token
    assert actions[0]["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"
    assert actions[0]["WFWorkflowActionParameters"]["WFTextActionText"] == "CLIENT_01"

    # Verify Action 1: Set Variable Tenant Token
    assert actions[1]["WFWorkflowActionIdentifier"] == "is.workflow.actions.setvariable"
    assert actions[1]["WFWorkflowActionParameters"]["WFVariableName"] == "Tenant Token"
    assert actions[1]["WFWorkflowActionParameters"]["WFInput"]["Value"]["OutputName"] == "Token Text"
    assert actions[1]["WFWorkflowActionParameters"]["WFInput"]["Value"]["OutputUUID"] == actions[0]["WFWorkflowActionParameters"]["UUID"]

    # Verify Action 2: Outer If (Shortcut Input has value)
    assert actions[2]["WFWorkflowActionIdentifier"] == "is.workflow.actions.conditional"
    assert actions[2]["WFWorkflowActionParameters"]["WFControlFlowMode"] == 0
    assert actions[2]["WFWorkflowActionParameters"]["WFCondition"] == 100

    # Verify Action 3: Inner If (Content does not contain DOORBELL)
    assert actions[3]["WFWorkflowActionIdentifier"] == "is.workflow.actions.conditional"
    assert actions[3]["WFWorkflowActionParameters"]["WFControlFlowMode"] == 0
    assert actions[3]["WFWorkflowActionParameters"]["WFCondition"] == 999
    assert actions[3]["WFWorkflowActionParameters"]["WFConditionalActionString"] == "DOORBELL"

    # Verify Action 4: Inbound POST
    assert actions[4]["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
    assert actions[4]["WFWorkflowActionParameters"]["WFHTTPMethod"] == "POST"
    assert "webhook/inbound" in actions[4]["WFWorkflowActionParameters"]["WFURL"]

    # Verify Action 5: Exit (Stop This Shortcut)
    assert actions[5]["WFWorkflowActionIdentifier"] == "is.workflow.actions.exit"

    # Verify Actions 6 & 7: End Inner If & End Outer If
    assert actions[6]["WFWorkflowActionParameters"]["WFControlFlowMode"] == 2
    assert actions[7]["WFWorkflowActionParameters"]["WFControlFlowMode"] == 2

    # Verify Action 9: Outbound GET with dynamic token range
    assert actions[9]["WFWorkflowActionParameters"]["WFHTTPMethod"] == "GET"
    url_str = actions[9]["WFWorkflowActionParameters"]["WFURL"]["Value"]["string"]
    assert "webhook/outbound?token=" in url_str
    assert "{56, 1}" in actions[9]["WFWorkflowActionParameters"]["WFURL"]["Value"]["attachmentsByRange"]
    assert url_str[56:57] == "\ufffc"

    # Verify Action 21 & 23: SendMessage (MMS & SMS)
    assert actions[21]["WFWorkflowActionIdentifier"] == "is.workflow.actions.sendmessage"
    assert actions[21]["WFWorkflowActionParameters"]["ShowWhenRun"] is False
    assert actions[23]["WFWorkflowActionIdentifier"] == "is.workflow.actions.sendmessage"
    assert actions[23]["WFWorkflowActionParameters"]["ShowWhenRun"] is False

    # Verify Action 25: Sent Ack POST
    assert actions[25]["WFWorkflowActionParameters"]["WFHTTPMethod"] == "POST"
    assert "webhook/sent" in actions[25]["WFWorkflowActionParameters"]["WFURL"]

    # 2. Test Download Endpoint GET /AI_Booking_Relay_Master.shortcut
    res_dl = client.get("/AI_Booking_Relay_Master.shortcut")
    assert res_dl.status_code == 200
    assert "attachment" in res_dl.headers.get("content-disposition", "")
    assert "AI_Booking_Relay_Master.shortcut" in res_dl.headers.get("content-disposition", "")
    assert len(res_dl.content) > 1000

    # 3. Test Static Alias GET /static/AI_Booking_Relay_Master.shortcut
    res_alias = client.get("/static/AI_Booking_Relay_Master.shortcut")
    assert res_alias.status_code == 200
    assert len(res_alias.content) == len(res_dl.content)

    # 4. Test Download Endpoint GET /AI_Booking_Relay_Master.plist
    res_plist = client.get("/AI_Booking_Relay_Master.plist")
    assert res_plist.status_code == 200
    assert "attachment" in res_plist.headers.get("content-disposition", "")
    assert "AI_Booking_Relay_Master.plist" in res_plist.headers.get("content-disposition", "")
    assert "xml" in res_plist.headers.get("content-type", "")

    # 5. Test Static Alias GET /static/AI_Booking_Relay_Master.plist
    res_plist_alias = client.get("/static/AI_Booking_Relay_Master.plist")
    assert res_plist_alias.status_code == 200
    assert len(res_plist_alias.content) == len(res_plist.content)

@pytest.mark.asyncio
async def test_gateway_heartbeat_worker(monkeypatch, capsys):
    import asyncio
    import contextlib
    import urllib.error
    import urllib.request
    from backend.services.relay_service import start_gateway_heartbeat_worker

    # 1. Successful heartbeat check
    class DummyResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass

    monkeypatch.setenv("ANDROID_GATEWAY_WAKEUP_URL", "https://heartbeat.gateway/ping")
    monkeypatch.setenv("GATEWAY_HEARTBEAT_INTERVAL_SECONDS", "0") # immediate for test
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=5: DummyResp())

    task = asyncio.create_task(start_gateway_heartbeat_worker(interval_seconds=0))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    captured = capsys.readouterr()
    assert "[Gateway Heartbeat OK]" in captured.out
    assert "status 200" in captured.out

    # 2. Unreachable / timed out gateway
    def failing_urlopen(req, timeout=5):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
    task2 = asyncio.create_task(start_gateway_heartbeat_worker(interval_seconds=0))
    await asyncio.sleep(0.05)
    task2.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task2

    captured2 = capsys.readouterr()
    assert "[Gateway Heartbeat WARNING]" in captured2.out
    assert "offline or unreachable" in captured2.out

def test_analyze_plist_file_shortcut_download(client):
    import os

    # 1. Download endpoint GET /Analyze_Plist_File.shortcut
    res = client.get("/Analyze_Plist_File.shortcut")
    assert res.status_code == 200
    assert "attachment" in res.headers.get("content-disposition", "")
    assert "Analyze_Plist_File" in res.headers.get("content-disposition", "")
    assert len(res.content) == 30380

    # 2. Static alias GET /static/Analyze_Plist_File.shortcut
    res_alias = client.get("/static/Analyze_Plist_File.shortcut")
    assert res_alias.status_code == 200
    assert len(res_alias.content) == 30380

    # 3. Duplicate download endpoint GET /Analyze_Plist_File_(1).shortcut
    res_dup = client.get("/Analyze_Plist_File_(1).shortcut")
    assert res_dup.status_code == 200
    assert len(res_dup.content) == 30380

    # 4. Duplicate static alias GET /static/Analyze_Plist_File_(1).shortcut
    res_dup_alias = client.get("/static/Analyze_Plist_File_(1).shortcut")
    assert res_dup_alias.status_code == 200
    assert len(res_dup_alias.content) == 30380

    # 5. Pushbullet duplicate endpoint GET /Analyze_Plist_File_(2).shortcut
    res_dup2 = client.get("/Analyze_Plist_File_(2).shortcut")
    assert res_dup2.status_code == 200
    assert len(res_dup2.content) == 30380

    # 6. Pushbullet duplicate static alias GET /static/Analyze_Plist_File_(2).shortcut
    res_dup2_alias = client.get("/static/Analyze_Plist_File_(2).shortcut")
    assert res_dup2_alias.status_code == 200
    assert len(res_dup2_alias.content) == 30380

    # 7. Pushbullet duplicate endpoint GET /Analyze_Plist_File_(3).shortcut
    res_dup3 = client.get("/Analyze_Plist_File_(3).shortcut")
    assert res_dup3.status_code == 200
    assert len(res_dup3.content) == 30380

    # 8. Pushbullet duplicate static alias GET /static/Analyze_Plist_File_(3).shortcut
    res_dup3_alias = client.get("/static/Analyze_Plist_File_(3).shortcut")
    assert res_dup3_alias.status_code == 200
    assert len(res_dup3_alias.content) == 30380

def test_outbound_untokened_query_fallback_and_token_stripping(client, test_db):
    from backend.models.domain import OutboundQueueMessage

    # 1. Enqueue an outbound message for a specific tenant
    enqueue_res = client.post("/webhook/outbound", json={
        "phone": "+61412345678",
        "reply": "Your booking is confirmed!",
        "token": "CLIENT_01"
    })
    assert enqueue_res.status_code == 200
    msg_id = enqueue_res.json()["id"]

    # 2. Dequeue with untokened query (?token= or empty) - must fall back and return the message
    pending_res = client.get("/webhook/outbound?token=")
    assert pending_res.status_code == 200
    data = pending_res.json()
    assert data["has_message"] == "yes"
    assert data["id"] == msg_id
    assert data["phone"] == "+61412345678"

    # 3. Verify telemetry stripping trailing newlines and whitespace
    log_res = client.post("/webhook/log", json={
        "event": "DOORBELL_WOKE_UP",
        "token": "CLIENT_01\n\n",
        "source": "ios_shortcut"
    })
    assert log_res.status_code == 200
    assert log_res.json()["status"] == "logged"
    assert log_res.json()["token"] == "CLIENT_01"



def test_doorbell_inbound_ping_filtered(client):
    # 1. Exact string from Central Android Gateway
    res1 = client.post("/webhook/inbound", json={
        "sender": "+61436281573",
        "message": "DOORBELL PING from Central Android Gateway"
    })
    assert res1.status_code == 200
    assert res1.json() == {"status": "success", "event": "doorbell_ack"}

    # 2. Case-insensitive "doorbell" in body
    res2 = client.post("/webhook/inbound", json={
        "sender": "+61436281573",
        "message": "Doorbell wake-up signal"
    })
    assert res2.status_code == 200
    assert res2.json() == {"status": "success", "event": "doorbell_ack"}

    # 3. Form-encoded field variations
    res3 = client.post("/webhook/inbound", data={
        "From": "+61436281573",
        "Body": "DOORBELL ping"
    })
    assert res3.status_code == 200
    assert res3.json() == {"status": "success", "event": "doorbell_ack"}

def test_gateway_vcf_asset_endpoint(client):
    res = client.get("/api/v1/relay/assets/gateway.vcf")
    assert res.status_code == 200
    assert "text/vcard" in res.headers.get("content-type", "")
    assert "gateway.vcf" in res.headers.get("content-disposition", "")
    text = res.text
    assert "BEGIN:VCARD" in text
    assert "VERSION:3.0" in text
    assert "FN:AI Booking Sync" in text
    assert "TEL;TYPE=CELL:+61432172148" in text
    assert "END:VCARD" in text


def test_onboarding_send_contact_endpoint(client, test_db, monkeypatch):
    from backend.models.domain import OutboundQueueMessage

    # Mock gateway wakeup
    wakeup_called = False
    def fake_wakeup(force=False):
        nonlocal wakeup_called
        wakeup_called = True
        return True

    monkeypatch.setattr("backend.routes.sms.trigger_gateway_wakeup", fake_wakeup)

    payload = {
        "phone": "+61436281573",
        "token": "CLIENT_01",
    }
    res = client.post("/api/v1/relay/onboarding/send-contact", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["delivery"] == "queued"
    assert data["phone"] == "+61436281573"
    assert data["token"] == "CLIENT_01"
    assert "gateway.vcf" in data["vcard_url"]
    assert "AI Booking Sync" in data["message"]
    assert wakeup_called is True

    # Verify message in database
    with test_db() as db:
        msg = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id == data["id"]).first()
        assert msg is not None
        assert msg.phone == "+61436281573"
        assert msg.client_id == "CLIENT_01"
        assert msg.status == "pending"
        assert "gateway.vcf" in msg.message


def test_unified_ai_booking_relay_shortcut_generation_and_endpoints(client):
    import os
    import plistlib
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    shortcut_file = os.path.join(repo_root, "AI_Booking_Relay.shortcut")
    plist_file = os.path.join(repo_root, "AI_Booking_Relay.plist")

    assert os.path.exists(shortcut_file), "AI_Booking_Relay.shortcut does not exist"
    assert os.path.exists(plist_file), "AI_Booking_Relay.plist does not exist"

    # 1. Parse binary shortcut
    with open(shortcut_file, "rb") as f:
        data = plistlib.load(f)

    assert data.get("WFWorkflowHasShortcutInputVariables") is True
    actions = data.get("WFWorkflowActions", [])
    assert len(actions) == 29, f"Expected 29 actions, got {len(actions)}"

    # Single Import Question
    questions = data.get("WFWorkflowImportQuestions", [])
    assert len(questions) == 1
    assert questions[0]["ParameterKey"] == "WFTextActionText"
    assert "CLIENT_01" in questions[0]["DefaultValue"]
    assert questions[0]["Text"] == "Enter your Booking System Client Token:"

    # Action 0: Text Token
    assert actions[0]["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"
    assert actions[0]["WFWorkflowActionParameters"]["CustomOutputName"] == "Token Text"
    assert actions[0]["WFWorkflowActionParameters"]["WFTextActionText"] == "CLIENT_01"

    # Action 1: Set Variable Tenant Token
    assert actions[1]["WFWorkflowActionIdentifier"] == "is.workflow.actions.setvariable"
    assert actions[1]["WFWorkflowActionParameters"]["WFVariableName"] == "Tenant Token"

    # Action 2: Trigger Details
    assert actions[2]["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"
    assert actions[2]["WFWorkflowActionParameters"]["CustomOutputName"] == "Trigger Details"

    # Action 3: Trigger Matches regex
    assert actions[3]["WFWorkflowActionIdentifier"] == "is.workflow.actions.text.match"
    assert actions[3]["WFWorkflowActionParameters"]["CustomOutputName"] == "Trigger Matches"
    assert "432172148" in actions[3]["WFWorkflowActionParameters"]["WFMatchTextPattern"]

    # Action 4: If Trigger Matches (Branch on sender / trigger)
    assert actions[4]["WFWorkflowActionIdentifier"] == "is.workflow.actions.conditional"
    assert actions[4]["WFWorkflowActionParameters"]["WFControlFlowMode"] == 0

    # True Branch: Outbound Mode
    assert actions[5]["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
    assert "webhook/log" in actions[5]["WFWorkflowActionParameters"]["WFURL"]
    assert actions[6]["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
    assert actions[6]["WFWorkflowActionParameters"]["WFHTTPMethod"] == "GET"
    assert "webhook/outbound?token=" in actions[6]["WFWorkflowActionParameters"]["WFURL"]["Value"]["string"]

    # Check ShowWhenRun: False on all Send Message actions
    send_msg_actions = [a for a in actions if a.get("WFWorkflowActionIdentifier") == "is.workflow.actions.sendmessage"]
    assert len(send_msg_actions) >= 2
    for sa in send_msg_actions:
        assert sa["WFWorkflowActionParameters"]["ShowWhenRun"] is False

    # Check Action 22: Sent Receipt POST
    assert actions[22]["WFWorkflowActionParameters"]["WFHTTPMethod"] == "POST"
    assert "webhook/sent" in actions[22]["WFWorkflowActionParameters"]["WFURL"]

    # Check Action 26: Otherwise (Inbound Mode)
    assert actions[26]["WFWorkflowActionIdentifier"] == "is.workflow.actions.conditional"
    assert actions[26]["WFWorkflowActionParameters"]["WFControlFlowMode"] == 1

    # Check Action 27: Inbound POST
    assert actions[27]["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
    assert actions[27]["WFWorkflowActionParameters"]["WFHTTPMethod"] == "POST"
    assert "webhook/inbound" in actions[27]["WFWorkflowActionParameters"]["WFURL"]
    inbound_items = actions[27]["WFWorkflowActionParameters"]["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
    keys = [item["WFKey"]["Value"]["string"] for item in inbound_items]
    assert "token" in keys
    assert "sender" in keys
    assert "message" in keys
    assert "source" in keys

    # Check Action 28: End If
    assert actions[28]["WFWorkflowActionIdentifier"] == "is.workflow.actions.conditional"
    assert actions[28]["WFWorkflowActionParameters"]["WFControlFlowMode"] == 2

    # 2. Test Download Endpoint GET /AI_Booking_Relay.shortcut
    res_dl = client.get("/AI_Booking_Relay.shortcut")
    assert res_dl.status_code == 200
    assert "attachment" in res_dl.headers.get("content-disposition", "")
    assert "AI_Booking_Relay.shortcut" in res_dl.headers.get("content-disposition", "")
    assert len(res_dl.content) > 1000

    # 3. Test Static Alias GET /static/AI_Booking_Relay.shortcut
    res_alias = client.get("/static/AI_Booking_Relay.shortcut")
    assert res_alias.status_code == 200
    assert len(res_alias.content) == len(res_dl.content)

    # 4. Test Download Endpoint GET /AI_Booking_Relay.plist
    res_plist = client.get("/AI_Booking_Relay.plist")
    assert res_plist.status_code == 200
    assert "attachment" in res_plist.headers.get("content-disposition", "")
    assert "AI_Booking_Relay.plist" in res_plist.headers.get("content-disposition", "")

    # 5. Test Static Alias GET /static/AI_Booking_Relay.plist
    res_plist_alias = client.get("/static/AI_Booking_Relay.plist")
    assert res_plist_alias.status_code == 200
    assert len(res_plist_alias.content) == len(res_plist.content)

def test_install_baked_shortcut_endpoint(client):
    import plistlib

    # 1. Bake with phone
    res_phone = client.get("/api/v1/relay/install?phone=+61436281573")
    assert res_phone.status_code == 200
    assert "attachment" in res_phone.headers.get("content-disposition", "")
    assert res_phone.headers.get("X-Baked-Token") == "+61436281573"

    baked_data = plistlib.loads(res_phone.content)
    assert baked_data.get("WFWorkflowImportQuestions") == [] # ZERO popup questions!
    actions = baked_data.get("WFWorkflowActions", [])
    assert actions[0]["WFWorkflowActionParameters"]["WFTextActionText"] == "+61436281573"

    # 2. Bake with token via /relay/install alias
    res_token = client.get("/relay/install?token=SALON_PRO_01")
    assert res_token.status_code == 200
    assert res_token.headers.get("X-Baked-Token") == "SALON_PRO_01"

    baked_data2 = plistlib.loads(res_token.content)
    assert baked_data2.get("WFWorkflowImportQuestions") == []
    actions2 = baked_data2.get("WFWorkflowActions", [])
    assert actions2[0]["WFWorkflowActionParameters"]["WFTextActionText"] == "SALON_PRO_01"


def test_inbound_graceful_fallback_for_ios_shortcuts(client):
    # When source is "ios_shortcut", missing or invalid sender/body must return 200
    res_missing_sender = client.post("/webhook/inbound", json={
        "source": "ios_shortcut",
        "message": "Hello Tori!",
    })
    assert res_missing_sender.status_code == 200
    assert res_missing_sender.json()["status"] == "received"
    assert res_missing_sender.json()["action"] == "none"

    # Missing body
    res_missing_body = client.post("/webhook/inbound", json={
        "source": "ios_shortcut",
        "sender": "+61412345678",
    })
    assert res_missing_body.status_code == 200
    assert res_missing_body.json()["status"] == "received"
    assert res_missing_body.json()["action"] == "none"

    # Standard non-shortcut API caller without source still gets 422
    res_standard = client.post("/webhook", json={"message": "No sender"})
    assert res_standard.status_code == 422


def test_queue_retry_cap_and_failure_isolation(test_db):
    import asyncio
    from backend.models.domain import OutboundQueueMessage
    from backend.services.relay_service import start_stranded_relay_worker

    with test_db() as db:
        # Create a message that has failed/retried 3 times
        exhausted_msg = OutboundQueueMessage(
            id="exhausted-msg-1",
            phone="+61436281573",
            message="Test message",
            status="pending",
            retry_count=3,
        )
        db.add(exhausted_msg)
        db.commit()

    # Run check_stranded_and_retry_caps on database
    from backend.services.relay_service import check_stranded_and_retry_caps
    with test_db() as db:
        check_stranded_and_retry_caps(db)

    with test_db() as db:
        msg = db.query(OutboundQueueMessage).filter(OutboundQueueMessage.id == "exhausted-msg-1").first()
        assert msg is not None
        assert msg.status == "failed"

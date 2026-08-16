import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import main
from main import ArrivalSession, CalendarEvent, PushSubscription, SessionLocal, app


client = TestClient(app)


def _cleanup():
    db = SessionLocal()
    try:
        db.query(PushSubscription).filter(PushSubscription.endpoint.like("https://push.test/%")).delete(
            synchronize_session=False
        )
        db.query(ArrivalSession).filter(ArrivalSession.booking_id == "push-arrival-booking").delete(
            synchronize_session=False
        )
        db.query(CalendarEvent).filter(CalendarEvent.id == "push-arrival-booking").delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def test_vapid_keypair_is_generated_once_and_remains_stable(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    first_private, first_public = main._ensure_persistent_vapid_keypair()
    second_private, second_public = main._ensure_persistent_vapid_keypair()
    assert first_private == second_private
    assert first_public == second_public
    assert len(first_public) == 87
    assert (tmp_path / "vapid_private.pem").read_text().startswith("-----BEGIN PRIVATE KEY-----")
    assert (tmp_path / "vapid_public.txt").read_text() == first_public


def test_push_subscription_is_saved_without_returning_capability_keys(monkeypatch):
    _cleanup()
    monkeypatch.setattr(main, "WEB_PUSH_AVAILABLE", True)
    monkeypatch.setattr(main, "_vapid_private_key", lambda: "test-key.pem")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public-key")
    payload = {
        "endpoint": "https://push.test/device-one",
        "expirationTime": None,
        "keys": {"p256dh": "p" * 80, "auth": "a" * 20},
    }
    try:
        response = client.post("/api/push/subscriptions", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "subscribed"}
        config = client.get("/api/push/config")
        assert config.status_code == 200
        assert config.json()["activeSubscriptions"] >= 1
        assert "endpoint" not in config.text
        assert "p256dh" not in config.text
    finally:
        _cleanup()


def test_arrival_push_uses_nonsensitive_payload_and_disables_expired_subscription(monkeypatch):
    _cleanup()
    monkeypatch.setattr(main, "WEB_PUSH_AVAILABLE", True)
    monkeypatch.setattr(main, "_vapid_private_key", lambda: "test-key.pem")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public-key")
    delivered = []

    class Gone(Exception):
        response = type("Response", (), {"status_code": 410})()

    def fake_webpush(**kwargs):
        delivered.append(kwargs)
        if kwargs["subscription_info"]["endpoint"].endswith("gone"):
            raise Gone()

    monkeypatch.setattr(main, "webpush", fake_webpush)
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        db.add(CalendarEvent(
            id="push-arrival-booking", summary="Massage booking", customer_phone="+61400000000",
            start_time=now, end_time=now + timedelta(hours=1), status="scheduled", notes="",
        ))
        db.add(ArrivalSession(
            id="push-arrival-session", booking_id="push-arrival-booking",
            invite_token_hash="invite-secret-hash", status="active",
            expires_at=now + timedelta(hours=2), activated_at=now,
        ))
        for suffix in ("live", "gone"):
            db.add(PushSubscription(
                endpoint=f"https://push.test/{suffix}", p256dh="p" * 80, auth="a" * 20,
            ))
        db.commit()
    finally:
        db.close()

    try:
        main.send_arrival_push_notifications("push-arrival-session")
        assert len(delivered) == 2
        push_payload = json.loads(delivered[0]["data"])
        assert push_payload["url"] == "/arrivals?session=push-arrival-session"
        assert "invite" not in delivered[0]["data"].lower()
        assert "+61400000000" not in delivered[0]["data"]
        db = SessionLocal()
        try:
            assert db.query(PushSubscription).filter(
                PushSubscription.endpoint == "https://push.test/gone"
            ).one().active is False
        finally:
            db.close()
    finally:
        _cleanup()

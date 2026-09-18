"""Focused tests for the central ntfy notification integrations."""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from backend.services import notification_service
from backend.routes.booking import _queue_booking_notification


class _Response:
    def raise_for_status(self):
        return None


def test_deduplicated_central_notification_sends_once(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    main.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        notification_service,
        "_dyn",
        lambda name, fallback=None: factory if name == "SessionLocal" else fallback,
    )
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    deliveries = []
    monkeypatch.setattr(
        notification_service.requests,
        "post",
        lambda *args, **kwargs: deliveries.append((args, kwargs)) or _Response(),
    )

    payload = {
        "notification_type": "booking_updated",
        "title": "Booking Updated",
        "message": "Appointment changed",
        "click_url": "/bookings",
        "dedupe_key": "booking:test:update-1",
    }
    assert notification_service.send_notification(**payload) is True
    assert notification_service.send_notification(**payload) is False
    assert len(deliveries) == 1


def test_notification_deep_link_uses_configured_origin_and_encodes_thread(monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test/")
    assert notification_service.build_notification_url("chat", thread="thread/a b") == (
        "https://app.example.test/chat?thread=thread%2Fa+b"
    )


def test_booking_notifications_are_post_commit_and_use_bookings_route(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_notification", lambda **kwargs: sent.append(kwargs) or True)
    monkeypatch.setattr(main, "build_notification_url", lambda path, **_query: f"https://app.test{path}")
    start = datetime.utcnow() + timedelta(hours=1)
    booking = main.CalendarEvent(
        id="booking-notification-test",
        summary="Alex - Massage (Tori)",
        start_time=start,
        end_time=start + timedelta(hours=1),
        status="scheduled",
    )

    _queue_booking_notification(
        None,
        booking,
        notification_type="new_booking",
        title="New Booking",
        priority=4,
    )

    assert sent[0]["title"] == "New Booking"
    assert sent[0]["click_url"] == "https://app.test/bookings"
    assert sent[0]["dedupe_key"].startswith("booking:")


def test_thread_attention_notification_is_deduplicated_per_unresolved_event(monkeypatch):
    captured = []
    monkeypatch.setattr(notification_service, "send_notification", lambda **kwargs: captured.append(kwargs) or True)
    notification_service.send_thread_attention_notification(
        thread_id="thread-1",
        sms_account_key="secondary",
        reason="AI reply missed",
        source_key="message-1",
    )
    assert captured[0]["title"] == "SMS Needs Review"
    assert captured[0]["priority"] == 4
    assert captured[0]["dedupe_key"] == "thread-review:thread-1:message-1"

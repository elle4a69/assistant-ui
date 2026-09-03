from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)
TEST_IDS = {"alert-feed-past", "alert-feed-current", "booking-amount-update"}


def _cleanup() -> None:
    db = main.SessionLocal()
    try:
        db.query(main.CalendarEvent).filter(main.CalendarEvent.id.in_(TEST_IDS)).delete(
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


def test_default_booking_feed_excludes_history_but_history_view_can_request_it(monkeypatch):
    _cleanup()
    monkeypatch.setattr(main.calendar_service, "service", None)
    now = datetime.now(ZoneInfo("Australia/Hobart")).replace(tzinfo=None)
    db = main.SessionLocal()
    try:
        db.add_all([
            main.CalendarEvent(
                id="alert-feed-past",
                summary="Past booking",
                start_time=now - timedelta(days=16, minutes=30),
                end_time=now - timedelta(days=16),
                status="scheduled",
                notes="",
            ),
            main.CalendarEvent(
                id="alert-feed-current",
                summary="Current booking",
                start_time=now + timedelta(minutes=30),
                end_time=now + timedelta(hours=1),
                status="scheduled",
                notes="",
            ),
        ])
        db.commit()

        default_response = client.get("/api/calendar/bookings")
        assert default_response.status_code == 200
        default_ids = {booking["id"] for booking in default_response.json()}
        assert "alert-feed-current" in default_ids
        assert "alert-feed-past" not in default_ids

        history_response = client.get("/api/calendar/bookings?includePast=true")
        assert history_response.status_code == 200
        history_ids = {booking["id"] for booking in history_response.json()}
        assert TEST_IDS <= history_ids
    finally:
        db.close()
        _cleanup()


def test_booking_amount_can_be_corrected_without_changing_other_fields(monkeypatch):
    _cleanup()
    monkeypatch.setattr(main.calendar_service, "service", None)
    start = datetime.now(ZoneInfo("Australia/Hobart")).replace(tzinfo=None)
    db = main.SessionLocal()
    try:
        db.add(main.CalendarEvent(
            id="booking-amount-update",
            summary="Customer - Full Service",
            start_time=start,
            end_time=start + timedelta(minutes=30),
            status="completed",
            notes="Existing notes",
        ))
        db.commit()

        response = client.put(
            "/api/calendar/bookings/booking-amount-update",
            json={"amount": 250},
        )
        assert response.status_code == 200
        assert response.json()["amount"] == 250

        booking = db.query(main.CalendarEvent).filter_by(id="booking-amount-update").one()
        db.refresh(booking)
        assert booking.amount == 250
        assert booking.summary == "Customer - Full Service"
        assert booking.status == "completed"
        assert booking.notes == "Existing notes"

        invalid = client.put(
            "/api/calendar/bookings/booking-amount-update",
            json={"amount": -1},
        )
        assert invalid.status_code == 422
    finally:
        db.close()
        _cleanup()

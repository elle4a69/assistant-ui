from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)
TEST_IDS = {"alert-feed-past", "alert-feed-current", "booking-amount-update", "booking-natural-update"}


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
        assert {"alert-feed-past", "alert-feed-current"} <= history_ids
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


def test_explicit_natural_edit_persists_and_adjusts_total_once(monkeypatch):
    _cleanup()
    monkeypatch.setattr(main.calendar_service, "service", None)
    start = datetime.now(ZoneInfo("Australia/Hobart")).replace(tzinfo=None)
    db = main.SessionLocal()
    try:
        db.add(main.CalendarEvent(
            id="booking-natural-update",
            summary="Customer - Any Service (Tori)",
            start_time=start,
            end_time=start + timedelta(minutes=30),
            amount=225,
        ))
        db.commit()

        selected = client.put(
            "/api/calendar/bookings/booking-natural-update",
            json={"extras": ["natural"]},
        )
        assert selected.status_code == 200
        assert selected.json()["amount"] == 325
        assert selected.json()["extras"] == [{"id": "natural", "name": "Natural", "price": 100}]

        selected_again = client.put(
            "/api/calendar/bookings/booking-natural-update",
            json={"extras": ["natural"]},
        )
        assert selected_again.json()["amount"] == 325

        removed = client.put(
            "/api/calendar/bookings/booking-natural-update",
            json={"extras": []},
        )
        assert removed.json()["amount"] == 225
        assert removed.json()["extras"] == []
    finally:
        db.close()
        _cleanup()


def test_calendar_sync_imports_natural_only_from_explicit_metadata(monkeypatch):
    class FakeEvents:
        def list(self, **_kwargs):
            return self

        def execute(self):
            start = datetime.now(ZoneInfo("Australia/Hobart")) + timedelta(days=1)
            return {"items": [
                {
                    "id": "synced-natural",
                    "summary": "Customer - Any Service (Tori)",
                    "description": "Customer phone: +61400000000",
                    "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": (start + timedelta(minutes=30)).isoformat()},
                    "extendedProperties": {"private": {"booking_extras": '["natural"]'}},
                },
                {
                    "id": "synced-standard",
                    "summary": "Customer - Any Service (Tori)",
                    "description": "Customer phone: +61400000001",
                    "start": {"dateTime": (start + timedelta(hours=1)).isoformat()},
                    "end": {"dateTime": (start + timedelta(hours=1, minutes=30)).isoformat()},
                },
            ]}

    class FakeCalendarService:
        def events(self):
            return FakeEvents()

    monkeypatch.setattr(main.calendar_service, "service", FakeCalendarService())
    monkeypatch.setattr(main, "load_line_services", lambda _key: [
        {"id": "any-service", "name": "Any Service", "price": 225, "duration": 30},
    ])

    response = client.get("/api/calendar/bookings")
    assert response.status_code == 200
    synced = next(item for item in response.json() if item["id"] == "synced-natural")
    assert synced["extras"] == [{"id": "natural", "name": "Natural", "price": 100}]
    assert synced["amount"] == 325
    standard = next(item for item in response.json() if item["id"] == "synced-standard")
    assert standard["extras"] == []
    assert standard["amount"] == 225

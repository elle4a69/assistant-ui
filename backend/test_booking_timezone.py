from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, CalendarEvent, UpdateBookingInput, parse_business_datetime


def test_booking_timestamp_converts_utc_to_australian_standard_time():
    local = parse_business_datetime("2026-08-09T05:00:00Z")

    assert local.isoformat() == "2026-08-09T15:00:00+10:00"
    assert local.strftime("%A at %I:%M %p") == "Sunday at 03:00 PM"


def test_booking_timestamp_observes_australian_daylight_saving_time():
    local = parse_business_datetime("2026-12-13T04:00:00Z")

    assert local.isoformat() == "2026-12-13T15:00:00+11:00"
    assert local.strftime("%A at %I:%M %p") == "Sunday at 03:00 PM"


def test_naive_confirmed_evening_time_is_interpreted_as_local_not_utc():
    local = parse_business_datetime("2026-08-25T20:00:00")

    assert local.isoformat() == "2026-08-25T20:00:00+10:00"
    assert local.hour == 20


def test_booking_update_preserves_explicit_local_evening_hour(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    db.add(CalendarEvent(
        id="evening-booking",
        summary="Evening appointment",
        start_time=datetime(2026, 8, 25, 18, 0),
        end_time=datetime(2026, 8, 25, 18, 30),
    ))
    db.commit()
    monkeypatch.setattr(main.calendar_service, "service", None)

    result = main.update_booking_endpoint(
        "evening-booking",
        UpdateBookingInput(
            startTime="2026-08-25T20:00:00",
            endTime="2026-08-25T20:30:00",
        ),
        db,
    )

    stored = db.get(CalendarEvent, "evening-booking")
    assert stored.start_time == datetime(2026, 8, 25, 20, 0)
    assert result["startTime"] == "2026-08-25T20:00:00+10:00"
    db.close()

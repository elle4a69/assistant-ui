from main import parse_business_datetime


def test_booking_timestamp_converts_utc_to_australian_standard_time():
    local = parse_business_datetime("2026-08-09T05:00:00Z")

    assert local.isoformat() == "2026-08-09T15:00:00+10:00"
    assert local.strftime("%A at %I:%M %p") == "Sunday at 03:00 PM"


def test_booking_timestamp_observes_australian_daylight_saving_time():
    local = parse_business_datetime("2026-12-13T04:00:00Z")

    assert local.isoformat() == "2026-12-13T15:00:00+11:00"
    assert local.strftime("%A at %I:%M %p") == "Sunday at 03:00 PM"


def test_naive_calendar_edit_is_interpreted_as_hobart_business_time():
    local = parse_business_datetime("2026-09-03T21:00:00")

    assert local.isoformat() == "2026-09-03T21:00:00+10:00"

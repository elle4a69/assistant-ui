import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from booking_tools import BookingToolSuite, LegacyCalendarDiscoveryProvider


TZ = ZoneInfo("Australia/Melbourne")
RECEIVED = datetime(2026, 9, 9, 1, 24, 38, tzinfo=timezone.utc)
REQUESTED = datetime(2026, 9, 10, 10, 0, tzinfo=TZ)


def suite(busy=()):
    return BookingToolSuite(LegacyCalendarDiscoveryProvider(
        services_loader=lambda: [{"id": "service", "name": "Service", "duration": 60}],
        working_hours_loader=lambda: [{"day": "Thursday", "enabled": True, "open": "09:00", "close": "17:00"}],
        busy_slots_loader=lambda _start, _end: list(busy), timezone_name="Australia/Melbourne",
    ), "Australia/Melbourne", now_factory=lambda: datetime(2026, 9, 9, 10, tzinfo=TZ))


def test_incident_variations_parse_from_customer_message_time():
    for text in ("tomorrow morning around 10am", "around 10am tomorrow", "tomorrow at 10", "10 tomorrow morning", "about 10 tomorrow", "10:00am tomorrow"):
        assert main.parse_customer_requested_slot(text, RECEIVED) == REQUESTED


def test_exact_10am_is_checked_not_inferred_from_capped_morning_list():
    tools = suite([{"start": datetime(2026, 9, 10, 19, tzinfo=TZ), "end": datetime(2026, 9, 10, 20, tzinfo=TZ)}])
    assert all("T10:" not in item["start_time"] for item in tools.times_tomorrow("service", 8)["slots"])
    result = tools.check_exact_time("service", REQUESTED.isoformat())
    assert result["available"] is True and result["exact_slot"]["start_time"] == REQUESTED.isoformat()


def test_unavailable_10am_has_nearest_before_and_after():
    tools = suite([{"start": REQUESTED, "end": REQUESTED + timedelta(hours=1)}])
    result = tools.check_exact_time("service", REQUESTED.isoformat())
    assert result["available"] is False
    assert result["nearest_before"]["start_time"] == "2026-09-10T09:00:00+10:00"
    assert result["nearest_after"]["start_time"] == "2026-09-10T11:00:00+10:00"


def test_unknown_service_is_not_silently_invented():
    assert main.explicitly_requested_service("tomorrow morning at 10am", [{"id": "service", "name": "Service"}]) is None


def test_exact_time_schema_requires_service_and_start():
    schema = next(item for item in __import__("booking_tools").BOOKING_DISCOVERY_TOOL_SCHEMAS if item["name"] == "check_exact_time")
    assert schema["parameters"]["required"] == ["service_id", "start_time"]


def test_customer_timestamp_not_worker_time_controls_tomorrow_date():
    assert main.parse_customer_requested_slot("tomorrow at 10", RECEIVED).date().isoformat() == "2026-09-10"

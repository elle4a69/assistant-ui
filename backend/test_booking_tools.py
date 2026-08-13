from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from booking_tools import BookingToolSuite, LegacyCalendarDiscoveryProvider


TIMEZONE = "Australia/Hobart"
TZ = ZoneInfo(TIMEZONE)


class FakeProvider:
    def __init__(self):
        self.searches = []

    def list_services(self):
        return [{"id": "7", "name": "Consult", "duration_minutes": 30}]

    def search_availability(self, service_id, start, end, limit):
        self.searches.append((service_id, start, end, limit))
        return [{
            "service_id": service_id,
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=30)).isoformat(),
        }][:limit]


def test_suite_exposes_current_time_services_and_day_shortcuts():
    now = datetime(2026, 8, 12, 10, 7, tzinfo=TZ)
    provider = FakeProvider()
    suite = BookingToolSuite(provider, TIMEZONE, now_factory=lambda: now)

    assert suite.current_time() == {
        "status": "ok",
        "timezone": TIMEZONE,
        "current_time": now.isoformat(),
        "local_date": "2026-08-12",
        "weekday": "Wednesday",
    }
    assert suite.services()["services"][0]["id"] == "7"
    assert suite.times_today("7")["date"] == "2026-08-12"
    assert suite.times_tomorrow("7")["date"] == "2026-08-13"
    assert provider.searches[0][1] == now
    assert provider.searches[1][1] == datetime(2026, 8, 13, 0, 0, tzinfo=TZ)


def test_next_available_respects_after_and_returns_one_slot():
    now = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)
    provider = FakeProvider()
    suite = BookingToolSuite(provider, TIMEZONE, now_factory=lambda: now)

    result = suite.next_available("7", "2026-08-14T09:00:00+10:00")

    assert result["next_available"]["start_time"] == "2026-08-14T09:00:00+10:00"
    assert provider.searches[0][3] == 1


def test_legacy_adapter_uses_service_duration_hours_and_busy_slots():
    busy_start = datetime(2026, 8, 12, 9, 0, tzinfo=TZ)
    provider = LegacyCalendarDiscoveryProvider(
        services_loader=lambda: [{
            "id": "massage",
            "name": "Massage",
            "duration": 30,
            "price": 100,
        }],
        working_hours_loader=lambda: [{
            "day": "Wednesday",
            "enabled": True,
            "open": "09:00",
            "close": "10:30",
        }],
        busy_slots_loader=lambda start, end: [{
            "start": busy_start,
            "end": busy_start + timedelta(minutes=30),
        }],
        timezone_name=TIMEZONE,
    )

    slots = provider.search_availability(
        "massage",
        datetime(2026, 8, 12, 8, 55, tzinfo=TZ),
        datetime(2026, 8, 12, 11, 0, tzinfo=TZ),
        3,
    )

    assert [slot["start_time"] for slot in slots] == [
        "2026-08-12T09:30:00+10:00",
        "2026-08-12T09:45:00+10:00",
        "2026-08-12T10:00:00+10:00",
    ]


def test_one_hour_booking_requires_four_consecutive_fifteen_minute_increments():
    provider = LegacyCalendarDiscoveryProvider(
        services_loader=lambda: [{
            "id": "pse-60",
            "name": "One hour PSE",
            "duration": 60,
            "price": 600,
        }],
        working_hours_loader=lambda: [{
            "day": "Thursday",
            "enabled": True,
            "open": "13:00",
            "close": "16:00",
        }],
        # A single occupied 15-minute increment at 2pm invalidates every
        # one-hour start whose complete interval would overlap it.
        busy_slots_loader=lambda start, end: [{
            "start": datetime(2026, 8, 13, 14, 0, tzinfo=TZ),
            "end": datetime(2026, 8, 13, 14, 15, tzinfo=TZ),
        }],
        timezone_name=TIMEZONE,
        slot_interval_minutes=15,
    )

    times = provider.search_availability(
        "pse-60",
        datetime(2026, 8, 13, 13, 0, tzinfo=TZ),
        datetime(2026, 8, 13, 16, 0, tzinfo=TZ),
        10,
    )

    assert [item["start_time"] for item in times] == [
        "2026-08-13T13:00:00+10:00",
        "2026-08-13T14:15:00+10:00",
        "2026-08-13T14:30:00+10:00",
        "2026-08-13T14:45:00+10:00",
        "2026-08-13T15:00:00+10:00",
    ]
    assert all(
        main_end - main_start == timedelta(minutes=60)
        for main_start, main_end in (
            (datetime.fromisoformat(item["start_time"]), datetime.fromisoformat(item["end_time"]))
            for item in times
        )
    )


def test_execute_returns_safe_error_for_unknown_service():
    provider = LegacyCalendarDiscoveryProvider(
        services_loader=lambda: [],
        working_hours_loader=lambda: [],
        busy_slots_loader=lambda start, end: [],
        timezone_name=TIMEZONE,
    )
    suite = BookingToolSuite(provider, TIMEZONE)

    result = suite.execute("get_times_today", {"service_id": "missing"})

    assert result == {"status": "unavailable", "reason": "That service is not available."}

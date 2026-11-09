"""Tests for availability configuration, working_hours synchronization, and custom slot filtering."""

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient

from backend.main import (
    app,
    AUTH_COOKIE_NAME,
    _admin_session_token,
    calendar_service,
)
import backend.main as main_mod
from backend.services.booking_service import (
    load_availability_config,
    booking_availability_error,
    validate_booking_request,
    load_working_hours,
    current_business_time,
)
from backend.booking_tools import LegacyCalendarDiscoveryProvider

TZ = ZoneInfo("Australia/Hobart")


def get_auth_client() -> TestClient:
    client = TestClient(app)
    expires_at = int(datetime.now(timezone.utc).timestamp()) + 3600
    client.cookies.set(AUTH_COOKIE_NAME, _admin_session_token(expires_at))
    return client


def test_get_availability_settings_defaults(tmp_path, monkeypatch):
    test_avail_path = str(tmp_path / "availability_config.json")
    test_wh_path = str(tmp_path / "nonexistent_wh.json")
    monkeypatch.setattr("backend.routes.settings.AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr("backend.services.booking_service.AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr("backend.services.booking_service.WORKING_HOURS_PATH", test_wh_path)
    monkeypatch.setattr(main_mod, "WORKING_HOURS_PATH", test_wh_path)

    client = get_auth_client()
    response = client.get("/api/settings/availability")
    assert response.status_code == 200
    data = response.json()
    assert data["maxDaysAhead"] == 14
    assert len(data["weeklyHours"]) == 7

    # Check Monday (default open 09:00, close 17:00 -> 16 30-minute slots)
    mon = next(d for d in data["weeklyHours"] if d["day"] == "Monday")
    assert mon["enabled"] is True
    assert mon["open"] == "09:00"
    assert mon["close"] == "17:00"
    assert mon["slots"][0] == "09:00"
    assert mon["slots"][-1] == "16:30"
    assert len(mon["slots"]) == 16

    # Verify availability_config.json was created
    assert os.path.exists(test_avail_path)


def test_post_availability_settings_syncs_working_hours(tmp_path, monkeypatch):
    test_avail_path = str(tmp_path / "availability_config.json")
    test_wh_path = str(tmp_path / "working_hours.json")

    monkeypatch.setattr("backend.routes.settings.AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr("backend.services.booking_service.AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr("backend.routes.settings.WORKING_HOURS_PATH", test_wh_path)
    monkeypatch.setattr("backend.services.booking_service.WORKING_HOURS_PATH", test_wh_path)
    monkeypatch.setattr(main_mod, "AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr(main_mod, "WORKING_HOURS_PATH", test_wh_path)

    client = get_auth_client()

    payload = {
        "maxDaysAhead": 7,
        "weeklyHours": [
            {
                "day": "Monday",
                "enabled": True,
                "slots": ["10:00", "10:30", "11:00", "14:00", "14:30"],
            },
            {
                "day": "Tuesday",
                "enabled": False,
                "slots": [],
            },
            {
                "day": "Wednesday",
                "enabled": True,
                "slots": ["09:00", "09:30"],
            },
            {"day": "Thursday", "enabled": False, "slots": []},
            {"day": "Friday", "enabled": False, "slots": []},
            {"day": "Saturday", "enabled": False, "slots": []},
            {"day": "Sunday", "enabled": False, "slots": []},
        ],
    }

    res = client.post("/api/settings/availability", json=payload)
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["status"] == "success"
    assert res_data["maxDaysAhead"] == 7

    # Check working_hours.json backwards-compatible synchronization
    with open(test_wh_path, "r", encoding="utf-8") as f:
        synced_wh = json.load(f)

    mon_wh = next(d for d in synced_wh if d["day"] == "Monday")
    assert mon_wh["enabled"] is True
    # open = min(slots) = 10:00, close = max(slots) + 30m = 14:30 + 30m = 15:00
    assert mon_wh["open"] == "10:00"
    assert mon_wh["close"] == "15:00"

    wed_wh = next(d for d in synced_wh if d["day"] == "Wednesday")
    assert wed_wh["enabled"] is True
    assert wed_wh["open"] == "09:00"
    assert wed_wh["close"] == "10:00"


def test_booking_availability_error_with_custom_slots(tmp_path, monkeypatch):
    test_avail_path = str(tmp_path / "availability_config.json")
    monkeypatch.setattr("backend.services.booking_service.AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr(main_mod, "AVAILABILITY_CONFIG_PATH", test_avail_path)

    # Monday has custom slots: only 10:00, 10:30, 14:00 (11:00-13:30 is closed/lunch)
    config = {
        "maxDaysAhead": 14,
        "weeklyHours": [
            {
                "day": "Monday",
                "enabled": True,
                "slots": ["10:00", "10:30", "14:00"],
            },
            {
                "day": "Tuesday",
                "enabled": False,
                "slots": [],
            }
        ],
    }
    with open(test_avail_path, "w", encoding="utf-8") as f:
        json.dump(config, f)

    fake_now = datetime(2026, 8, 10, 8, 0, tzinfo=TZ)  # Monday 8am
    monkeypatch.setattr(main_mod, "current_business_time", lambda: fake_now)
    monkeypatch.setattr("backend.services.booking_service.current_business_time", lambda: fake_now)
    # Also fake calendar get_busy_slots
    monkeypatch.setattr(calendar_service, "get_busy_slots", lambda start, end, **kw: [])

    # Monday 10:00 for 30 mins -> spans [10:00] -> in slots -> OK
    dt_valid_30 = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    assert booking_availability_error(dt_valid_30, 30) is None
    assert validate_booking_request(dt_valid_30, 30) is None

    # Monday 10:00 for 60 mins -> spans [10:00, 10:30] -> in slots -> OK
    assert booking_availability_error(dt_valid_30, 60) is None

    # Monday 10:30 for 60 mins -> spans [10:30, 11:00] -> 11:00 is missing -> Error
    dt_invalid_end = datetime(2026, 8, 10, 10, 30, tzinfo=TZ)
    assert booking_availability_error(dt_invalid_end, 60) == "The full appointment does not fit within working hours."

    # Monday 12:00 -> not in slots -> Error
    dt_lunch = datetime(2026, 8, 10, 12, 0, tzinfo=TZ)
    assert booking_availability_error(dt_lunch, 30) == "The full appointment does not fit within working hours."

    # Tuesday -> closed
    dt_tue = datetime(2026, 8, 11, 10, 0, tzinfo=TZ)
    assert booking_availability_error(dt_tue, 30) == "The business is closed at that time."


def test_freebusy_filtering_with_custom_slots(tmp_path, monkeypatch):
    custom_config = {
        "maxDaysAhead": 2,
        "weeklyHours": [
            {
                "day": "Monday",
                "enabled": True,
                "slots": ["09:00", "09:30"],  # 09:00 to 10:00 window
            },
            {"day": "Tuesday", "enabled": False, "slots": []},
            {"day": "Wednesday", "enabled": False, "slots": []},
            {"day": "Thursday", "enabled": False, "slots": []},
            {"day": "Friday", "enabled": False, "slots": []},
            {"day": "Saturday", "enabled": False, "slots": []},
            {"day": "Sunday", "enabled": False, "slots": []},
        ],
    }
    monkeypatch.setattr(main_mod, "load_availability_config", lambda: custom_config)
    monkeypatch.setattr("backend.routes.booking.load_availability_config", lambda: custom_config)
    monkeypatch.setattr(calendar_service, "get_busy_slots", lambda start, end, **kw: [])

    # Freeze time to Monday 08:00
    fake_now = datetime(2026, 8, 10, 8, 0, tzinfo=TZ)
    from datetime import datetime as real_datetime
    class MockDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now

    monkeypatch.setattr("backend.routes.booking.datetime", MockDatetime)

    client = get_auth_client()
    res = client.get("/api/calendar/freebusy?duration=30")
    assert res.status_code == 200
    slots = res.json()
    hobart_starts = [
        datetime.fromisoformat(s["startTime"].replace("Z", "+00:00")).astimezone(TZ).strftime("%H:%M")
        for s in slots
    ]
    # In a 09:00-10:00 window, 15-min increments with 30-min duration:
    # 09:00 (09:00-09:30), 09:15 (09:15-09:45), 09:30 (09:30-10:00)
    assert hobart_starts == ["09:00", "09:15", "09:30"]


def test_legacy_calendar_provider_search_availability_with_slots():
    provider = LegacyCalendarDiscoveryProvider(
        services_loader=lambda: [{"id": "s1", "name": "Service", "duration": 30}],
        working_hours_loader=lambda: [{
            "day": "Wednesday",
            "enabled": True,
            "slots": ["09:00", "10:00"],  # 09:30 is missing
        }],
        busy_slots_loader=lambda start, end: [],
        timezone_name="Australia/Hobart",
    )

    # 30-min service on Wednesday from 09:00 to 11:00
    start = datetime(2026, 8, 12, 9, 0, tzinfo=TZ)
    end = datetime(2026, 8, 12, 11, 0, tzinfo=TZ)
    slots = provider.search_availability("s1", start, end, 10)

    start_times = [s["start_time"] for s in slots]
    # 09:00 is valid (spans 09:00)
    assert "2026-08-12T09:00:00+10:00" in start_times
    # 09:30 is invalid because 09:30 is missing from slots
    assert "2026-08-12T09:30:00+10:00" not in start_times
    # 10:00 is valid (spans 10:00)
    assert "2026-08-12T10:00:00+10:00" in start_times


def test_post_working_hours_syncs_availability_config(tmp_path, monkeypatch):
    test_avail_path = str(tmp_path / "availability_config.json")
    test_wh_path = str(tmp_path / "working_hours.json")

    # Seed existing availability_config with custom maxDaysAhead = 21
    initial_avail = {
        "maxDaysAhead": 21,
        "weeklyHours": [],
    }
    with open(test_avail_path, "w", encoding="utf-8") as f:
        json.dump(initial_avail, f)

    monkeypatch.setattr("backend.routes.settings.AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr("backend.services.booking_service.AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr("backend.routes.settings.WORKING_HOURS_PATH", test_wh_path)
    monkeypatch.setattr("backend.services.booking_service.WORKING_HOURS_PATH", test_wh_path)
    monkeypatch.setattr(main_mod, "AVAILABILITY_CONFIG_PATH", test_avail_path)
    monkeypatch.setattr(main_mod, "WORKING_HOURS_PATH", test_wh_path)

    client = get_auth_client()

    payload = {
        "hours": [
            {"day": "Monday", "enabled": True, "open": "08:30", "close": "11:30"},
            {"day": "Tuesday", "enabled": False, "open": "09:00", "close": "17:00"},
            {"day": "Wednesday", "enabled": True, "open": "13:00", "close": "14:30"},
            {"day": "Thursday", "enabled": False, "open": "09:00", "close": "17:00"},
            {"day": "Friday", "enabled": False, "open": "09:00", "close": "17:00"},
            {"day": "Saturday", "enabled": False, "open": "10:00", "close": "14:00"},
            {"day": "Sunday", "enabled": False, "open": "10:00", "close": "14:00"},
        ]
    }

    res = client.post("/api/settings/working-hours", json=payload)
    assert res.status_code == 200
    assert res.json() == {"status": "success"}

    # Verify working_hours.json
    with open(test_wh_path, "r", encoding="utf-8") as f:
        saved_wh = json.load(f)
    assert len(saved_wh) == 7
    assert saved_wh[0]["day"] == "Monday"
    assert saved_wh[0]["open"] == "08:30"

    # Verify availability_config.json was synchronized:
    # 1. maxDaysAhead (21) preserved
    # 2. Monday expanded into 08:30, 09:00, 09:30, 10:00, 10:30, 11:00 (6 slots)
    # 3. Tuesday enabled=False, slots=[]
    # 4. Wednesday expanded into 13:00, 13:30, 14:00 (3 slots)
    with open(test_avail_path, "r", encoding="utf-8") as f:
        synced_avail = json.load(f)

    assert synced_avail["maxDaysAhead"] == 21
    mon_avail = next(d for d in synced_avail["weeklyHours"] if d["day"] == "Monday")
    assert mon_avail["enabled"] is True
    assert mon_avail["open"] == "08:30"
    assert mon_avail["close"] == "11:30"
    assert mon_avail["slots"] == ["08:30", "09:00", "09:30", "10:00", "10:30", "11:00"]

    tue_avail = next(d for d in synced_avail["weeklyHours"] if d["day"] == "Tuesday")
    assert tue_avail["enabled"] is False
    assert tue_avail["slots"] == []

    wed_avail = next(d for d in synced_avail["weeklyHours"] if d["day"] == "Wednesday")
    assert wed_avail["enabled"] is True
    assert wed_avail["slots"] == ["13:00", "13:30", "14:00"]

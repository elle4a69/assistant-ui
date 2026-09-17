"""Unit tests for Phase 8: Tenant-Scoped Timezone Handling."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest

from config.timezone import (
    TenantTimezoneError,
    ConfigurationError,
    validate_iana_timezone,
    get_tenant_timezone,
    get_tenant_now,
    get_tenant_date_boundaries,
    format_tenant_time,
    register_tenant_timezone,
    clear_tenant_timezones,
)
from booking_tools import BookingToolSuite, build_booking_tool_suite, BookingDiscoveryProvider


class DummyBookingProvider:
    """Mock discovery provider for testing BookingToolSuite integration."""

    def __init__(self) -> None:
        self.recorded_queries: list[dict] = []

    def list_services(self) -> list[dict]:
        return [{"id": "s1", "name": "Consultation", "price": "$100", "duration_minutes": 30}]

    def search_availability(self, service_id: str, start: datetime, end: datetime, limit: int) -> list[dict]:
        self.recorded_queries.append({
            "service_id": service_id,
            "start": start,
            "end": end,
            "limit": limit,
        })
        return [{
            "service_id": service_id,
            "service_name": "Consultation",
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=30)).isoformat(),
        }]


@pytest.fixture(autouse=True)
def cleanup_tenant_registry():
    """Ensure in-memory registry is clear before and after every test."""
    clear_tenant_timezones()
    yield
    clear_tenant_timezones()


def test_1_valid_iana_timezone_configuration_loads_properly(monkeypatch, tmp_path):
    """Test 1: Valid IANA timezone configuration loads properly."""
    # Test valid IANA strings
    tz_hobart = validate_iana_timezone("Australia/Hobart")
    assert isinstance(tz_hobart, ZoneInfo)
    assert tz_hobart.key == "Australia/Hobart"

    tz_ny = validate_iana_timezone("America/New_York")
    assert tz_ny.key == "America/New_York"

    tz_london = validate_iana_timezone("Europe/London")
    assert tz_london.key == "Europe/London"

    tz_perth = validate_iana_timezone("Australia/Perth")
    assert tz_perth.key == "Australia/Perth"

    # Accepting a ZoneInfo instance directly
    assert validate_iana_timezone(tz_hobart) is tz_hobart

    # Explicit programmatic registry
    register_tenant_timezone("acct_prog", "Australia/Sydney")
    assert get_tenant_timezone("acct_prog") == ZoneInfo("Australia/Sydney")

    # Account-scoped environment variable
    monkeypatch.setenv("TENANT_TIMEZONE_ACCT_ENV", "America/Chicago")
    assert get_tenant_timezone("acct_env") == ZoneInfo("America/Chicago")

    # sms_line_profiles.json config
    profiles_file = tmp_path / "sms_line_profiles.json"
    profiles_file.write_text(json.dumps({
        "line_x": {"timezone": "Asia/Tokyo"}
    }), encoding="utf-8")
    monkeypatch.setenv("LINE_PROFILES_PATH", str(profiles_file))
    assert get_tenant_timezone("line_x") == ZoneInfo("Asia/Tokyo")


def test_2_invalid_iana_timezone_string_raises_tenant_timezone_error():
    """Test 2: Invalid IANA timezone string raises TenantTimezoneError."""
    # Non-existent timezone names
    with pytest.raises(TenantTimezoneError):
        validate_iana_timezone("Mars/Olympus_Mons")

    with pytest.raises(TenantTimezoneError):
        validate_iana_timezone("Not/A_Real_Timezone")

    # Empty or whitespace strings
    with pytest.raises(TenantTimezoneError):
        validate_iana_timezone("")

    with pytest.raises(TenantTimezoneError):
        validate_iana_timezone("   ")

    # Non-string / None values
    with pytest.raises(TenantTimezoneError):
        validate_iana_timezone(None)  # type: ignore

    with pytest.raises(TenantTimezoneError):
        validate_iana_timezone(12345)  # type: ignore

    # Verify ConfigurationError alias
    with pytest.raises(ConfigurationError):
        validate_iana_timezone("Invalid/Zone")


def test_3_missing_timezone_when_required_for_scheduling_raises_no_silent_fallback(monkeypatch, tmp_path):
    """Test 3: Missing timezone when required_for_scheduling=True raises TenantTimezoneError (no silent fallback)."""
    # 1. Unknown account with no config
    with pytest.raises(TenantTimezoneError) as exc_info:
        get_tenant_timezone("unknown_account_xyz", required_for_scheduling=True)
    assert "No timezone configured" in str(exc_info.value)
    assert "Silent fallback to global default is strictly disabled" in str(exc_info.value)

    # 2. Account profile in JSON with missing 'timezone' key
    profiles_file = tmp_path / "sms_line_profiles.json"
    profiles_file.write_text(json.dumps({
        "incomplete_acct": {"displayName": "No Timezone Line"}
    }), encoding="utf-8")
    monkeypatch.setenv("LINE_PROFILES_PATH", str(profiles_file))

    with pytest.raises(TenantTimezoneError) as exc_info:
        get_tenant_timezone("incomplete_acct", required_for_scheduling=True)
    assert "incomplete_acct" in str(exc_info.value)

    # 3. Account profile with empty string timezone
    profiles_file.write_text(json.dumps({
        "empty_tz_acct": {"displayName": "Empty TZ", "timezone": ""}
    }), encoding="utf-8")

    with pytest.raises(TenantTimezoneError) as exc_info:
        get_tenant_timezone("empty_tz_acct", required_for_scheduling=True)
    assert "empty timezone configured" in str(exc_info.value)

    # 4. Built-in default line present in JSON but missing explicit 'timezone' key
    profiles_file.write_text(json.dumps({
        "secondary": {"displayName": "Line 2"}
    }), encoding="utf-8")
    assert get_tenant_timezone("secondary", required_for_scheduling=True) == ZoneInfo("Australia/Hobart")

    # 5. When required_for_scheduling=False, it should NOT raise; returns None or fallback
    assert get_tenant_timezone("unknown_account_xyz", required_for_scheduling=False) is None
    assert get_tenant_timezone("unknown_account_xyz", required_for_scheduling=False, fallback="UTC") == ZoneInfo("UTC")


def test_4_tenant_date_boundaries_today_tomorrow_midnight_transitions():
    """Test 4: Tenant date boundaries (today, tomorrow, midnight UTC transitions)."""
    register_tenant_timezone("hobart_line", "Australia/Hobart")

    # Local afternoon: 2026-06-15 14:30:00+10:00
    ref_dt = datetime(2026, 6, 15, 14, 30, 0, tzinfo=ZoneInfo("Australia/Hobart"))
    boundaries = get_tenant_date_boundaries("hobart_line", dt=ref_dt)

    assert boundaries["today"] == date(2026, 6, 15)
    assert boundaries["tomorrow"] == date(2026, 6, 16)
    # Start of today (00:00:00 AEST = UTC+10) in UTC is previous day 14:00:00 UTC
    assert boundaries["start_of_today_utc"] == datetime(2026, 6, 14, 14, 0, 0, tzinfo=timezone.utc)
    # End of today in UTC
    assert boundaries["end_of_today_utc"] == datetime(2026, 6, 15, 13, 59, 59, 999999, tzinfo=timezone.utc)

    # Test crossing local midnight: 10 seconds before midnight vs 10 seconds after midnight
    before_midnight = datetime(2026, 6, 15, 23, 59, 50, tzinfo=ZoneInfo("Australia/Hobart"))
    after_midnight = datetime(2026, 6, 16, 0, 0, 10, tzinfo=ZoneInfo("Australia/Hobart"))

    b_before = get_tenant_date_boundaries("hobart_line", dt=before_midnight)
    b_after = get_tenant_date_boundaries("hobart_line", dt=after_midnight)

    assert b_before["today"] == date(2026, 6, 15)
    assert b_before["tomorrow"] == date(2026, 6, 16)
    assert b_before["start_of_today_utc"] == datetime(2026, 6, 14, 14, 0, 0, tzinfo=timezone.utc)

    assert b_after["today"] == date(2026, 6, 16)
    assert b_after["tomorrow"] == date(2026, 6, 17)
    assert b_after["start_of_today_utc"] == datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)

    # Test format_tenant_time formatting
    formatted_iso = format_tenant_time(ref_dt, "hobart_line")
    assert formatted_iso == "2026-06-15T14:30:00+10:00"

    formatted_custom = format_tenant_time(ref_dt, "hobart_line", fmt="%A at %I:%M %p")
    assert formatted_custom == "Monday at 02:30 PM"


def test_5_dst_transitions_southern_and_northern_hemispheres():
    """Test 5: DST transitions (spring forward, fall back) and UTC offsets."""
    register_tenant_timezone("hobart_line", "Australia/Hobart")
    register_tenant_timezone("ny_line", "America/New_York")

    # --- Southern Hemisphere (Australia/Hobart) ---
    # Fall back (AEDT UTC+11 -> AEST UTC+10) on first Sunday of April (2026-04-05)
    hobart_before_fallback = datetime(2026, 4, 5, 1, 59, tzinfo=ZoneInfo("Australia/Hobart"))
    hobart_after_fallback = datetime(2026, 4, 5, 3, 1, tzinfo=ZoneInfo("Australia/Hobart"))

    b_s_fb_before = get_tenant_date_boundaries("hobart_line", dt=hobart_before_fallback)
    b_s_fb_after = get_tenant_date_boundaries("hobart_line", dt=hobart_after_fallback)

    assert b_s_fb_before["is_dst"] is True
    assert hobart_before_fallback.utcoffset() == timedelta(hours=11)

    assert b_s_fb_after["is_dst"] is False
    assert hobart_after_fallback.utcoffset() == timedelta(hours=10)

    # Spring forward (AEST UTC+10 -> AEDT UTC+11) on first Sunday of October (2026-10-04)
    hobart_before_spring = datetime(2026, 10, 4, 1, 59, tzinfo=ZoneInfo("Australia/Hobart"))
    hobart_after_spring = datetime(2026, 10, 4, 3, 1, tzinfo=ZoneInfo("Australia/Hobart"))

    b_s_sf_before = get_tenant_date_boundaries("hobart_line", dt=hobart_before_spring)
    b_s_sf_after = get_tenant_date_boundaries("hobart_line", dt=hobart_after_spring)

    assert b_s_sf_before["is_dst"] is False
    assert hobart_before_spring.utcoffset() == timedelta(hours=10)

    assert b_s_sf_after["is_dst"] is True
    assert hobart_after_spring.utcoffset() == timedelta(hours=11)

    # --- Northern Hemisphere (America/New_York) ---
    # Spring forward (EST UTC-5 -> EDT UTC-4) on second Sunday of March (2026-03-08)
    ny_before_spring = datetime(2026, 3, 8, 1, 59, tzinfo=ZoneInfo("America/New_York"))
    ny_after_spring = datetime(2026, 3, 8, 3, 1, tzinfo=ZoneInfo("America/New_York"))

    b_n_sf_before = get_tenant_date_boundaries("ny_line", dt=ny_before_spring)
    b_n_sf_after = get_tenant_date_boundaries("ny_line", dt=ny_after_spring)

    assert b_n_sf_before["is_dst"] is False
    assert ny_before_spring.utcoffset() == timedelta(hours=-5)

    assert b_n_sf_after["is_dst"] is True
    assert ny_after_spring.utcoffset() == timedelta(hours=-4)

    # Fall back (EDT UTC-4 -> EST UTC-5) on first Sunday of November (2026-11-01)
    ny_before_fallback = datetime(2026, 11, 1, 0, 59, tzinfo=ZoneInfo("America/New_York"))
    ny_after_fallback = datetime(2026, 11, 1, 2, 1, tzinfo=ZoneInfo("America/New_York"))

    b_n_fb_before = get_tenant_date_boundaries("ny_line", dt=ny_before_fallback)
    b_n_fb_after = get_tenant_date_boundaries("ny_line", dt=ny_after_fallback)

    assert b_n_fb_before["is_dst"] is True
    assert ny_before_fallback.utcoffset() == timedelta(hours=-4)

    assert b_n_fb_after["is_dst"] is False
    assert ny_after_fallback.utcoffset() == timedelta(hours=-5)


def test_6_cross_timezone_tenant_isolation_across_midnight():
    """Test 6: Cross-timezone isolation between Hobart (UTC+10) and Perth (UTC+8)."""
    register_tenant_timezone("acct_hobart", "Australia/Hobart")
    register_tenant_timezone("acct_perth", "Australia/Perth")

    # Instant: 2026-06-15 at 15:30:00 UTC
    # In Hobart (UTC+10): 2026-06-16 01:30:00+10:00 (next calendar day)
    # In Perth (UTC+8):   2026-06-15 23:30:00+08:00 (previous calendar day)
    utc_instant = datetime(2026, 6, 15, 15, 30, 0, tzinfo=timezone.utc)

    b_hobart = get_tenant_date_boundaries("acct_hobart", dt=utc_instant)
    b_perth = get_tenant_date_boundaries("acct_perth", dt=utc_instant)

    assert b_hobart["today"] == date(2026, 6, 16)
    assert b_hobart["tomorrow"] == date(2026, 6, 17)
    assert b_hobart["local_now"].strftime("%Y-%m-%d %H:%M") == "2026-06-16 01:30"

    assert b_perth["today"] == date(2026, 6, 15)
    assert b_perth["tomorrow"] == date(2026, 6, 16)
    assert b_perth["local_now"].strftime("%Y-%m-%d %H:%M") == "2026-06-15 23:30"

    # Strict isolation: different calendar dates across the exact same UTC instant
    assert b_hobart["today"] != b_perth["today"]
    assert b_hobart["tomorrow"] != b_perth["tomorrow"]


def test_7_integration_with_booking_tool_suite():
    """Test 7: Integration with BookingToolSuite."""
    register_tenant_timezone("hobart_tenant", "Australia/Hobart")
    register_tenant_timezone("perth_tenant", "Australia/Perth")

    provider_hobart = DummyBookingProvider()
    provider_perth = DummyBookingProvider()

    # Fixed time factory returning 15:30 UTC
    utc_instant = datetime(2026, 6, 15, 15, 30, 0, tzinfo=timezone.utc)

    suite_hobart = BookingToolSuite(
        provider=provider_hobart,
        account_key="hobart_tenant",
        now_factory=lambda: utc_instant,
    )
    suite_perth = build_booking_tool_suite(
        provider=provider_perth,
        account_key="perth_tenant",
        now_factory=lambda: utc_instant,
    )

    # 1. Current time reflection
    res_hobart = suite_hobart.current_time()
    assert res_hobart["status"] == "ok"
    assert res_hobart["timezone"] == "Australia/Hobart"
    assert res_hobart["local_date"] == "2026-06-16"
    assert res_hobart["weekday"] == "Tuesday"

    res_perth = suite_perth.current_time()
    assert res_perth["status"] == "ok"
    assert res_perth["timezone"] == "Australia/Perth"
    assert res_perth["local_date"] == "2026-06-15"
    assert res_perth["weekday"] == "Monday"

    # 2. times_today searches should use the respective tenant local date
    suite_hobart.times_today("s1")
    suite_perth.times_today("s1")

    # In Hobart, today was June 16 -> search starts at 2026-06-16 01:30 Hobart time
    hobart_query = provider_hobart.recorded_queries[0]
    assert hobart_query["start"].date() == date(2026, 6, 16)

    # In Perth, today was June 15 -> search starts at 2026-06-15 23:30 Perth time
    perth_query = provider_perth.recorded_queries[0]
    assert perth_query["start"].date() == date(2026, 6, 15)

    # 3. Unconfigured account raises TenantTimezoneError when initializing BookingToolSuite
    with pytest.raises(TenantTimezoneError):
        BookingToolSuite(provider=provider_hobart, account_key="unconfigured_tenant_account")

    with pytest.raises(TenantTimezoneError):
        build_booking_tool_suite(provider=provider_hobart, account_key="unconfigured_tenant_account")

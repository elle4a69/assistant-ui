"""Tenant-scoped timezone handling.

Governing rules:
1. Remove silent reliance on a global default for tenant operations.
2. Mandatory tenant/account timezone configuration.
3. If a tenant/account requiring scheduling has no timezone configured, fail
   configuration validation (raise TenantTimezoneError) rather than silently
   defaulting to "Australia/Hobart".
4. All scheduling operations must resolve through the tenant timezone:
   - current time
   - date interpretation
   - today / tomorrow
   - booking availability
   - displayed booking times
   - expiry boundaries
   - scheduling cut-offs
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from backend.core.config import LINE_PROFILES_PATH, LINE_PROFILE_DEFAULTS
except ImportError:
    try:
        from core.config import LINE_PROFILES_PATH, LINE_PROFILE_DEFAULTS
    except ImportError:
        LINE_PROFILES_PATH = None
        LINE_PROFILE_DEFAULTS = None



class TenantTimezoneError(ValueError):
    """Raised when tenant timezone configuration is missing, invalid, or required for scheduling."""
    pass


ConfigurationError = TenantTimezoneError

# In-memory registry for programmatic tenant timezone configuration / test isolation
# Keys: (account_key, tenant_id) or (account_key, "*") or account_key
_TENANT_REGISTRY: Dict[Union[Tuple[str, str], str], ZoneInfo] = {}


def register_tenant_timezone(
    account_key: str,
    tz: Union[str, ZoneInfo],
    tenant_id: str = "default",
) -> None:
    """Explicitly register a timezone for a tenant account."""
    zone = validate_iana_timezone(tz)
    _TENANT_REGISTRY[(account_key, tenant_id)] = zone
    _TENANT_REGISTRY[account_key] = zone


def clear_tenant_timezones() -> None:
    """Clear programmatic tenant timezone registrations (useful in tests)."""
    _TENANT_REGISTRY.clear()


def validate_iana_timezone(tz_name: Union[str, ZoneInfo]) -> ZoneInfo:
    """Validate that tz_name is a valid IANA timezone and return ZoneInfo.

    Raises TenantTimezoneError if invalid or empty.
    """
    if isinstance(tz_name, ZoneInfo):
        return tz_name
    if not isinstance(tz_name, str) or not tz_name.strip():
        raise TenantTimezoneError(
            f"Invalid timezone: {tz_name!r}. Timezone must be a non-empty IANA string."
        )
    tz_str = tz_name.strip()
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, ValueError, Exception) as exc:
        raise TenantTimezoneError(
            f"Invalid IANA timezone '{tz_str}': {exc}"
        ) from exc


def _resolve_line_profiles_path() -> Optional[str]:
    """Resolve the path to sms_line_profiles.json."""
    if os.getenv("LINE_PROFILES_PATH"):
        return os.getenv("LINE_PROFILES_PATH")

    if LINE_PROFILES_PATH:
        return LINE_PROFILES_PATH

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    persist_dir = "/data" if os.path.exists("/data") else base_dir
    return os.path.join(persist_dir, "data", "sms_line_profiles.json")


def get_tenant_timezone(
    account_key: str,
    tenant_id: str = "default",
    required_for_scheduling: bool = True,
    fallback: Optional[Union[str, ZoneInfo]] = None,
) -> ZoneInfo | None:
    """Look up timezone for a tenant account.

    Resolution order:
    1. Explicit in-memory tenant registry.
    2. Tenant-scoped environment variables (e.g. TENANT_TIMEZONE_<ACCOUNT>, TIMEZONE_<ACCOUNT>, BOOKING_TIMEZONE_<ACCOUNT>).
    3. Account line profile configuration (sms_line_profiles.json).

    If required_for_scheduling is True:
        If no valid timezone is configured, raises TenantTimezoneError (no silent defaulting!).
    If required_for_scheduling is False:
        Can return fallback (or None if no fallback provided).
    """
    if not account_key:
        if required_for_scheduling:
            raise TenantTimezoneError("Account key must be provided to resolve tenant timezone.")
        if fallback is not None:
            return validate_iana_timezone(fallback)
        return None

    # 1. Explicit in-memory tenant registry
    if (account_key, tenant_id) in _TENANT_REGISTRY:
        return _TENANT_REGISTRY[(account_key, tenant_id)]
    if (account_key, "*") in _TENANT_REGISTRY:
        return _TENANT_REGISTRY[(account_key, "*")]
    if account_key in _TENANT_REGISTRY:
        return _TENANT_REGISTRY[account_key]

    # 2. Account-scoped environment variables
    env_keys = [
        f"TENANT_TIMEZONE_{account_key.upper()}",
        f"TIMEZONE_{account_key.upper()}",
        f"BOOKING_TIMEZONE_{account_key.upper()}",
        f"TENANT_{account_key.upper()}_TIMEZONE",
    ]
    if tenant_id and tenant_id != "default":
        env_keys.insert(0, f"TENANT_TIMEZONE_{tenant_id.upper()}_{account_key.upper()}")

    for env_key in env_keys:
        val = os.getenv(env_key)
        if val is not None:
            val_str = val.strip()
            if not val_str:
                if required_for_scheduling:
                    raise TenantTimezoneError(
                        f"Environment variable '{env_key}' specifies an empty timezone."
                    )
            else:
                return validate_iana_timezone(val_str)

    # 3. Line profile (sms_line_profiles.json)
    profiles_path = _resolve_line_profiles_path()
    if profiles_path and os.path.exists(profiles_path):
        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and account_key in data:
                profile = data[account_key]
                if isinstance(profile, dict):
                    tz_val = profile.get("timezone")
                    if tz_val is None:
                        tz_val = profile.get("time_zone")
                    if tz_val is not None:
                        if isinstance(tz_val, str) and not tz_val.strip():
                            if required_for_scheduling:
                                raise TenantTimezoneError(
                                    f"Tenant account '{account_key}' has empty timezone configured. "
                                    "A valid IANA timezone is mandatory for scheduling."
                                )
                        else:
                            return validate_iana_timezone(tz_val)
                    else:
                        # Profile exists in file but explicitly lacks timezone
                        if required_for_scheduling:
                            raise TenantTimezoneError(
                                f"Tenant account '{account_key}' profile has no timezone configured. "
                                "Tenant-scoped timezone is mandatory for scheduling."
                            )
        except TenantTimezoneError:
            raise
        except Exception:
            pass

    # 4. Built-in defaults in LINE_PROFILE_DEFAULTS (only if file does not exist on disk)
    if not (profiles_path and os.path.exists(profiles_path)):
        defaults = LINE_PROFILE_DEFAULTS
        if isinstance(defaults, dict) and account_key in defaults:
            profile = defaults[account_key]
            if isinstance(profile, dict):
                tz_val = profile.get("timezone") or profile.get("time_zone")
                if tz_val:
                    return validate_iana_timezone(tz_val)

    # 5. Fail validation if required for scheduling
    if required_for_scheduling:
        raise TenantTimezoneError(
            f"No timezone configured for account '{account_key}' (tenant '{tenant_id}'). "
            "Tenant-scoped timezone is mandatory for scheduling operations. "
            "Silent fallback to global default is strictly disabled."
        )

    if fallback is not None:
        return validate_iana_timezone(fallback)

    return None


def get_tenant_now(account_key: str, tenant_id: str = "default") -> datetime:
    """Return the current timezone-aware datetime in the tenant's configured timezone."""
    tz = get_tenant_timezone(account_key, tenant_id=tenant_id, required_for_scheduling=True)
    return datetime.now(tz)


def get_tenant_date_boundaries(
    account_key: str,
    tenant_id: str = "default",
    dt: Optional[datetime] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return local date boundaries and DST status for the tenant.

    Args:
        account_key: The SMS line or account key.
        tenant_id: Tenant identifier (defaults to "default").
        dt: Optional reference datetime. If provided, calculates boundaries
            relative to this instant. Otherwise uses current time.

    Returns:
        dict containing:
          - local_now: timezone-aware datetime in tenant's timezone
          - today: local date (datetime.date)
          - tomorrow: next day local date (datetime.date)
          - start_of_today_utc: UTC datetime corresponding to 00:00:00 local time today
          - end_of_today_utc: UTC datetime corresponding to 23:59:59.999999 local time today
          - is_dst: boolean indicating whether DST is in effect for local_now
    """
    tz = get_tenant_timezone(account_key, tenant_id=tenant_id, required_for_scheduling=True)
    ref = dt or kwargs.get("reference_time") or kwargs.get("now")
    if ref is None:
        local_now = datetime.now(tz)
    elif ref.tzinfo is None:
        local_now = ref.replace(tzinfo=tz)
    else:
        local_now = ref.astimezone(tz)

    today = local_now.date()
    tomorrow = today + timedelta(days=1)

    start_of_today_local = datetime.combine(today, time.min, tzinfo=tz)
    end_of_today_local = datetime.combine(today, time.max, tzinfo=tz)

    start_of_today_utc = start_of_today_local.astimezone(timezone.utc)
    end_of_today_utc = end_of_today_local.astimezone(timezone.utc)

    dst_offset = local_now.dst()
    is_dst = bool(dst_offset and dst_offset != timedelta(0))

    return {
        "local_now": local_now,
        "today": today,
        "tomorrow": tomorrow,
        "start_of_today_utc": start_of_today_utc,
        "end_of_today_utc": end_of_today_utc,
        "is_dst": is_dst,
    }


def format_tenant_time(
    dt: datetime,
    account_key: str,
    fmt: Optional[str] = None,
    tenant_id: str = "default",
) -> str:
    """Format a datetime in the tenant's configured timezone.

    If dt is naive, it is assumed to be in the tenant's timezone.
    If dt is timezone-aware, it is converted to the tenant's timezone.
    If fmt is None, returns ISO 8601 string.
    """
    tz = get_tenant_timezone(account_key, tenant_id=tenant_id, required_for_scheduling=True)
    if dt.tzinfo is None:
        dt_tenant = dt.replace(tzinfo=tz)
    else:
        dt_tenant = dt.astimezone(tz)

    if fmt is not None:
        return dt_tenant.strftime(fmt)
    return dt_tenant.isoformat()

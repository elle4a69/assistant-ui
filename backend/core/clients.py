"""Neutral API client singletons and external service infrastructure leaf module.

Contains singletons and factories for:
- OpenAI API client (`openai_client`)
- Google Calendar service / SQLite local calendar fallback (`calendar_service`, `GoogleCalendarService`)
- Twilio client stub (`twilio_client`)
- GitHub Operations & OIDC clients (`operations_github_client`, `operations_github_oidc_verifier`)
- Supporting leaf helpers (`canonical_phone_number`, `resolve_provider_context`, etc.)

This module MUST NOT import anything from `backend.main` or any router module.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

try:
    import mobilemessage_service
except ImportError:
    from backend import mobilemessage_service

# Core configuration and database dependencies
try:
    from backend.core.config import (
        BASE_DIR,
        BUSINESS_VARIABLE_DEFAULTS,
        BUSINESS_VARIABLES_PATH,
        DATA_DIR,
        FIRST_CONTACT_ACCOUNT_KEYS,
        LINE_PROFILE_DEFAULTS,
        LINE_PROFILES_PATH,
    )
    from backend.core.database import Base, SessionLocal
    from backend.core.utils import _dyn
except ImportError:
    from core.config import (
        BASE_DIR,
        BUSINESS_VARIABLE_DEFAULTS,
        BUSINESS_VARIABLES_PATH,
        DATA_DIR,
        FIRST_CONTACT_ACCOUNT_KEYS,
        LINE_PROFILE_DEFAULTS,
        LINE_PROFILES_PATH,
    )
    from core.database import Base, SessionLocal
    from core.utils import _dyn

# Defensive imports for Google Calendar API
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    service_account = None
    build = None
    GOOGLE_LIBS_AVAILABLE = False

# Defensive imports for OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    OPENAI_AVAILABLE = False

# Defensive imports for Twilio
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TwilioClient = None
    TWILIO_AVAILABLE = False

# GitHub and Operations client services
try:
    from backend.github_oidc_service import GitHubOIDCError, GitHubOIDCVerifier
    from backend.operations_github_service import (
        OperationsGitHubClient,
        OperationsGitHubError,
        redact_sensitive_text,
    )
except ImportError:
    from github_oidc_service import GitHubOIDCError, GitHubOIDCVerifier
    from operations_github_service import (
        OperationsGitHubClient,
        OperationsGitHubError,
        redact_sensitive_text,
    )


# ===========================================================================
# Phone & Provider Leaf Utilities
# ===========================================================================

def canonical_phone_number(phone: str) -> str:
    """Format an Australian phone number into canonical E.164 string format (+614...)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("6104") and len(digits) == 12:
        digits = "614" + digits[4:]
    elif digits.startswith("614") and len(digits) == 11:
        pass
    elif digits.startswith("04") and len(digits) == 10:
        digits = "61" + digits[1:]
    elif digits.startswith("4") and len(digits) == 9:
        digits = "61" + digits
    if not digits.startswith("+") and digits.startswith("61"):
        return "+" + digits
    return phone.strip()


def _normalize_line_profile(account_key: str, value: Any) -> Dict[str, str]:
    profile = dict(LINE_PROFILE_DEFAULTS[account_key])
    if isinstance(value, dict):
        for key in profile:
            if value.get(key) is not None:
                profile[key] = str(value[key]).strip()
    return profile


def _resolve_line_profiles_path() -> str:
    """Resolve the current LINE_PROFILES_PATH dynamically to support test monkeypatching."""
    import sys
    for mod_name in ("main", "backend.main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "LINE_PROFILES_PATH"):
            val = getattr(mod, "LINE_PROFILES_PATH")
            if str(val) != str(LINE_PROFILES_PATH):
                return val
    for mod_name in ("main", "backend.main", "backend.core.config", "core.config"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "LINE_PROFILES_PATH"):
            return getattr(mod, "LINE_PROFILES_PATH")
    return LINE_PROFILES_PATH


def load_line_profiles() -> Dict[str, Dict[str, str]]:
    path = _resolve_line_profiles_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        saved = {}
    return {
        key: _normalize_line_profile(key, saved.get(key) if isinstance(saved, dict) else None)
        for key in FIRST_CONTACT_ACCOUNT_KEYS
    }


def save_line_profiles(profiles: Dict[str, Dict[str, str]]) -> None:
    path = _resolve_line_profiles_path()
    normalized = {key: _normalize_line_profile(key, profiles.get(key)) for key in FIRST_CONTACT_ACCOUNT_KEYS}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2, ensure_ascii=False)
    os.replace(temporary_path, path)


def get_line_profile(account_key: str) -> Dict[str, str]:
    """Return the saved customer-conversation profile for one SMS line."""
    if account_key not in FIRST_CONTACT_ACCOUNT_KEYS:
        account_key = "primary"
    return load_line_profiles()[account_key]



def resolve_provider_context(account_key: str) -> Dict[str, str]:
    """Resolve the only provider authority usable for one inbound SMS line."""
    if account_key not in FIRST_CONTACT_ACCOUNT_KEYS:
        raise ValueError("Unknown SMS account; provider context cannot be resolved.")
    get_profile = _dyn("get_line_profile", get_line_profile)
    profile = get_profile(account_key)
    provider_name = profile["providerName"].strip()
    if not provider_name:
        raise ValueError("The SMS line has no mapped provider.")
    return {
        "account_key": account_key,
        "sms_line": account_key,
        "provider_name": provider_name,
        "information_url": profile["informationUrl"].strip(),
    }


def _resolve_business_variables_path() -> str:
    """Resolve the current BUSINESS_VARIABLES_PATH dynamically to support test monkeypatching."""
    import sys
    for mod_name in ("main", "backend.main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "BUSINESS_VARIABLES_PATH"):
            val = getattr(mod, "BUSINESS_VARIABLES_PATH")
            if str(val) != str(BUSINESS_VARIABLES_PATH):
                return val
    for mod_name in ("main", "backend.main", "backend.core.config", "core.config"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "BUSINESS_VARIABLES_PATH"):
            return getattr(mod, "BUSINESS_VARIABLES_PATH")
    return BUSINESS_VARIABLES_PATH


def load_business_variables() -> List[Dict[str, Any]]:
    default_meta = {d["key"]: d for d in BUSINESS_VARIABLE_DEFAULTS}
    path = _resolve_business_variables_path()
    if not os.path.exists(path):
        raw_items = [dict(item) for item in BUSINESS_VARIABLE_DEFAULTS]
    else:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            if not isinstance(saved, list):
                raw_items = [dict(item) for item in BUSINESS_VARIABLE_DEFAULTS]
            else:
                raw_items = saved
        except Exception as exc:
            print(f"Failed to load business variables: {exc}")
            raw_items = [dict(item) for item in BUSINESS_VARIABLE_DEFAULTS]

    variables = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in seen:
            continue
        seen.add(key)

        meta = default_meta.get(key, {})
        label = str(item.get("label") or meta.get("label") or key.replace("_", " ").title()).strip()
        value = str(item.get("value", "")).strip()
        description = str(item.get("description") or meta.get("description") or f"Business detail for {label}").strip()
        is_required = bool(item.get("required") if "required" in item else meta.get("required", False))

        variables.append({
            "key": key,
            "token": f"{{{key}}}",
            "label": label,
            "value": value,
            "description": description,
            "required": is_required,
            "required_status": "required" if is_required else "optional",
        })
    return variables


def get_business_variable_values() -> Dict[str, str]:
    path = _resolve_business_variables_path()
    raw_vals = {
        item["key"]: item["value"]
        for item in load_business_variables()
        if item.get("value", "").strip()
    }
    website_val = raw_vals.get("website", "").strip() or raw_vals.get("booking_url", "").strip()
    if website_val:
        raw_vals["website"] = website_val
        raw_vals["booking_url"] = website_val

    if not os.path.exists(path) and not raw_vals:
        fallbacks = {
            "provider_name": "Tori",
            "suburb": "Melbourne",
            "website": "https://assistant-ui-hub.fly.dev/",
            "booking_url": "https://assistant-ui-hub.fly.dev/",
        }
        for k, v in fallbacks.items():
            if k not in raw_vals:
                raw_vals[k] = v

    return raw_vals


def get_line_business_variable_values(account_key: str) -> Dict[str, str]:
    """Add the selected line's identity fields without changing shared values."""
    resolve_ctx = _dyn("resolve_provider_context", resolve_provider_context)
    context = resolve_ctx(account_key)
    # The legacy global Settings file belongs to the original primary line.
    # It is never a secondary provider source; secondary values must be added
    # explicitly to that line profile/catalogue.
    get_biz_vars = _dyn("get_business_variable_values", get_business_variable_values)
    values = get_biz_vars() if account_key == "primary" else {}
    get_profile = _dyn("get_line_profile", get_line_profile)
    profile = get_profile(account_key)
    information_url = profile["informationUrl"].strip()
    if information_url:
        # Historical approved examples use {website}. For an SMS conversation,
        # that token must resolve to the receiving line's saved information link,
        # never a shared or other-line URL.
        values["website"] = information_url
        values["booking_url"] = information_url
    values.update({
        # The global Settings value may describe the other line. Identity is
        # always overwritten from the account resolved for this conversation.
        "provider_name": context["provider_name"],
        "line_key": account_key,
        "line_display_name": profile["displayName"],
        "line_provider_name": profile["providerName"],
        "line_information_url": information_url,
    })
    return values


def effective_line_user_prompt(account_key: str, shared_prompt: str) -> str:
    """Use an explicitly saved line prompt, falling back to the shared template."""
    get_profile = _dyn("get_line_profile", get_line_profile)
    line_prompt = get_profile(account_key)["userPrompt"].strip()
    return line_prompt or shared_prompt


# ===========================================================================
# Calendar Service & Singleton
# ===========================================================================

class GoogleCalendarService:
    _calendar_event_model = None

    def __init__(self, db_session_factory, calendar_event_model=None):
        self.db_session_factory = db_session_factory
        self._calendar_event_model = calendar_event_model
        self.service = None
        self._cache: Dict[tuple[str, str], tuple[float, List[Dict[str, datetime]]]] = {}
        scopes = ["https://www.googleapis.com/auth/calendar"]

        service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if service_account_json and GOOGLE_LIBS_AVAILABLE:
            try:
                credential_info = json.loads(service_account_json)
                creds = service_account.Credentials.from_service_account_info(
                    credential_info,
                    scopes=scopes,
                )
                self.service = build("calendar", "v3", credentials=creds)
                print("Google Calendar API initialized from encrypted environment credentials.")
                return
            except Exception as e:
                print(f"Failed to initialize Google Calendar API from environment: {e}.")

        paths_to_check = [
            os.path.join(BASE_DIR, "service_account.json"),
            os.path.join(BASE_DIR, "credentials.json"),
            "./service_account.json",
            "./credentials.json",
        ]

        cred_path = None
        for path in paths_to_check:
            if os.path.exists(path):
                cred_path = path
                break

        if cred_path and GOOGLE_LIBS_AVAILABLE:
            try:
                creds = service_account.Credentials.from_service_account_file(cred_path, scopes=scopes)
                self.service = build("calendar", "v3", credentials=creds)
                print(f"Google Calendar API initialized with: {cred_path}")
            except Exception as e:
                print(f"Failed to initialize Google Calendar API: {e}. Falling back to SQLite.")
        else:
            print("Google Calendar credentials not found. Falling back to local SQLite database-backed calendar.")

    def _get_calendar_event_model(self):
        """Dynamically resolve the CalendarEvent model class without importing main.py."""
        if getattr(self, "_calendar_event_model", None) is not None:
            return self._calendar_event_model
        try:
            from backend.models.domain import CalendarEvent
            return CalendarEvent
        except ImportError:
            try:
                from models.domain import CalendarEvent
                return CalendarEvent
            except ImportError:
                pass
        registry = getattr(Base, "registry", None)
        if registry is not None:
            cls = registry._class_registry.get("CalendarEvent")
            if cls is not None:
                return cls
            for mapper in getattr(registry, "mappers", []):
                if mapper.class_.__name__ == "CalendarEvent":
                    return mapper.class_
        decl_registry = getattr(Base, "_decl_class_registry", None)
        if decl_registry and "CalendarEvent" in decl_registry:
            return decl_registry["CalendarEvent"]
        return None

    def _get_db_session(self):
        if getattr(self, "db_session_factory", None) is not None:
            return self.db_session_factory()
        import sys
        for mod_name in ("backend.main", "main"):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "SessionLocal"):
                factory = getattr(mod, "SessionLocal")
                if factory is not None:
                    return factory()
        return None

    def get_busy_slots(
        self,
        start: datetime,
        end: datetime,
        *,
        require_authoritative: bool = False,
    ) -> List[Dict[str, datetime]]:
        tz_hobart = ZoneInfo("Australia/Hobart")

        # Ensure start and end are aware in Hobart timezone
        start_aware = start.astimezone(tz_hobart) if start.tzinfo is not None else start.replace(tzinfo=tz_hobart)
        end_aware = end.astimezone(tz_hobart) if end.tzinfo is not None else end.replace(tzinfo=tz_hobart)

        # Cache lookup
        cache_key = (start_aware.isoformat(), end_aware.isoformat())
        now_ts = time.time()
        if not require_authoritative and hasattr(self, "_cache") and cache_key in self._cache:
            cached_ts, cached_val = self._cache[cache_key]
            if now_ts - cached_ts < 20:  # 20 seconds cache TTL
                print("[Calendar Cache] Cache hit! Returning cached busy slots.")
                return cached_val

        # Fetch and parse busy slots
        parsed_busy = []
        google_success = False
        if self.service:
            try:
                calendar_id = os.getenv("CALENDAR_ID", "primary")
                body = {
                    "timeMin": start_aware.isoformat(),
                    "timeMax": end_aware.isoformat(),
                    "items": [{"id": calendar_id}],
                }
                res = self.service.freebusy().query(body=body).execute()
                busy_list = res.get("calendars", {}).get(calendar_id, {}).get("busy", [])

                for b in busy_list:
                    b_start = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tz_hobart)
                    b_end = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tz_hobart)
                    parsed_busy.append({"start": b_start, "end": b_end})
                google_success = True
            except Exception as e:
                if require_authoritative:
                    raise OSError("Live calendar availability could not be verified.") from e
                print(f"Error querying Google Calendar freebusy: {e}. Falling back to SQLite.")

        if not google_success:
            CalendarEvent = self._get_calendar_event_model()
            if CalendarEvent is not None:
                db = self._get_db_session()
                try:
                    start_naive = start_aware.replace(tzinfo=None)
                    end_naive = end_aware.replace(tzinfo=None)
                    events = db.query(CalendarEvent).filter(
                        (CalendarEvent.start_time < end_naive) & (CalendarEvent.end_time > start_naive)
                    ).all()
                    parsed_busy = [
                        {
                            "start": e.start_time.replace(tzinfo=tz_hobart),
                            "end": e.end_time.replace(tzinfo=tz_hobart),
                        }
                        for e in events
                    ]
                finally:
                    db.close()

        # Cache saving
        if not require_authoritative and hasattr(self, "_cache"):
            self._cache[cache_key] = (now_ts, parsed_busy)
        return parsed_busy

    def get_busy_slots_authoritative(
        self,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, datetime]]:
        """Fail closed instead of treating a Google outage as an empty calendar."""
        return self.get_busy_slots(start, end, require_authoritative=True)

    def get_busy_slots_for_account(
        self,
        start: datetime,
        end: datetime,
        sms_account_key: str,
        *,
        require_authoritative: bool = False,
    ) -> List[Dict[str, datetime]]:
        """Return shared-room occupancy after binding a valid SMS account."""
        try:
            resolve_provider_context(sms_account_key)
            return self.get_busy_slots(start, end, require_authoritative=require_authoritative)
        except (OSError, RuntimeError, ValueError) as exc:
            raise OSError("Shared calendar availability could not be verified.") from exc

    def get_customer_bookings(
        self,
        customer_phone: str,
        start: datetime,
        end: datetime,
        sms_account_key: str,
        db: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Return this customer's bookings owned by the selected SMS account."""
        resolve_provider_context(sms_account_key)

        tz_hobart = ZoneInfo("Australia/Hobart")
        start_aware = start.astimezone(tz_hobart) if start.tzinfo else start.replace(tzinfo=tz_hobart)
        end_aware = end.astimezone(tz_hobart) if end.tzinfo else end.replace(tzinfo=tz_hobart)
        canonical_customer = canonical_phone_number(customer_phone)
        results: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        if self.service:
            try:
                calendar_id = os.getenv("CALENDAR_ID", "primary")
                response = self.service.events().list(
                    calendarId=calendar_id,
                    timeMin=start_aware.isoformat(),
                    timeMax=end_aware.isoformat(),
                    orderBy="startTime",
                    singleEvents=True,
                ).execute()
                for event_item in response.get("items", []):
                    description = event_item.get("description", "") or ""
                    private = event_item.get("extendedProperties", {}).get("private", {})
                    if private.get("sms_account_key") != sms_account_key:
                        continue
                    event_phone = private.get("customer_phone")
                    if not event_phone and "Customer phone:" in description:
                        event_phone = description.split("Customer phone:", 1)[1].splitlines()[0].strip()
                    if canonical_phone_number(event_phone or "") != canonical_customer:
                        continue
                    start_raw = event_item.get("start", {}).get("dateTime")
                    end_raw = event_item.get("end", {}).get("dateTime")
                    if not start_raw or not end_raw:
                        continue
                    event_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(tz_hobart)
                    event_end = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(tz_hobart)
                    key = (event_start.isoformat(), event_end.isoformat())
                    seen.add(key)
                    results.append({
                        "id": event_item.get("id"),
                        "summary": event_item.get("summary", "Appointment"),
                        "start": event_start,
                        "end": event_end,
                    })
            except Exception as exc:
                print(f"Error listing customer Google Calendar bookings: {exc}")

        owns_session = db is None
        local_db = db or self._get_db_session()
        try:
            CalendarEvent = self._get_calendar_event_model()
            if CalendarEvent is not None:
                local_events = local_db.query(CalendarEvent).filter(
                    CalendarEvent.sms_account_key == sms_account_key,
                    CalendarEvent.start_time < end_aware.replace(tzinfo=None),
                    CalendarEvent.end_time > start_aware.replace(tzinfo=None),
                ).all()
                for event_item in local_events:
                    if canonical_phone_number(event_item.customer_phone or "") != canonical_customer:
                        continue
                    event_start = event_item.start_time.replace(tzinfo=tz_hobart)
                    event_end = event_item.end_time.replace(tzinfo=tz_hobart)
                    key = (event_start.isoformat(), event_end.isoformat())
                    if key in seen:
                        continue
                    results.append({
                        "id": event_item.id,
                        "summary": event_item.summary,
                        "start": event_start,
                        "end": event_end,
                    })
        finally:
            if owns_session:
                local_db.close()
        return sorted(results, key=lambda item: item["start"])

    def create_booking(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        customer_phone: str,
        sms_account_key: str = "primary",
    ) -> Optional[str]:
        provider_context = resolve_provider_context(sms_account_key)
        # Clear cache on modification
        if hasattr(self, "_cache"):
            self._cache.clear()

        tz_hobart = ZoneInfo("Australia/Hobart")

        # Ensure start and end are aware in Hobart timezone
        start_aware = start.astimezone(tz_hobart) if start.tzinfo is not None else start.replace(tzinfo=tz_hobart)
        end_aware = end.astimezone(tz_hobart) if end.tzinfo is not None else end.replace(tzinfo=tz_hobart)

        CalendarEvent = self._get_calendar_event_model()

        if self.service:
            try:
                calendar_id = os.getenv("CALENDAR_ID", "primary")
                event_body = {
                    "summary": summary,
                    "description": f"Customer phone: {customer_phone}",
                    "extendedProperties": {
                        "private": {
                            "customer_phone": canonical_phone_number(customer_phone),
                            "sms_account_key": sms_account_key,
                            "provider_name": provider_context["provider_name"],
                        }
                    },
                    "start": {
                        "dateTime": start_aware.isoformat(),
                    },
                    "end": {
                        "dateTime": end_aware.isoformat(),
                    },
                }
                created = self.service.events().insert(calendarId=calendar_id, body=event_body).execute() or {}
                # Mirror Google bookings locally so ownership remains available even when
                # free/busy only returns anonymous occupied intervals.
                booking_id = created.get("id") or str(uuid.uuid4())
                if CalendarEvent is not None:
                    db = self._get_db_session()
                    try:
                        booking = CalendarEvent(
                            id=booking_id,
                            customer_phone=customer_phone,
                            summary=summary,
                            start_time=start_aware.replace(tzinfo=None),
                            end_time=end_aware.replace(tzinfo=None),
                            sms_account_key=sms_account_key,
                        )
                        db.merge(booking)
                        db.commit()
                    except Exception as mirror_exc:
                        db.rollback()
                        print(f"Google booking created but local ownership mirror failed: {mirror_exc}")
                    finally:
                        db.close()
                return booking_id
            except Exception as e:
                print(f"Error creating Google Calendar booking: {e}. Falling back to SQLite.")

        if CalendarEvent is not None:
            db = self._get_db_session()
            try:
                booking_id = str(uuid.uuid4())
                booking = CalendarEvent(
                    id=booking_id,
                    customer_phone=customer_phone,
                    summary=summary,
                    start_time=start_aware.replace(tzinfo=None),
                    end_time=end_aware.replace(tzinfo=None),
                    sms_account_key=sms_account_key,
                )
                db.add(booking)
                db.commit()
                return booking_id
            except Exception as e:
                db.rollback()
                print(f"Failed to create booking in SQLite: {e}")
                return False
            finally:
                db.close()
        return False

    def delete_booking(self, booking_id: str) -> bool:
        # Clear cache on modification
        if hasattr(self, "_cache"):
            self._cache.clear()

        deleted_gc = False
        if self.service:
            try:
                calendar_id = os.getenv("CALENDAR_ID", "primary")
                self.service.events().delete(calendarId=calendar_id, eventId=booking_id).execute()
                deleted_gc = True
            except Exception as e:
                print(f"Error deleting Google Calendar booking {booking_id}: {e}.")

        CalendarEvent = self._get_calendar_event_model()
        if CalendarEvent is not None:
            db = self._get_db_session()
            try:
                booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()
                if booking:
                    db.delete(booking)
                    db.commit()
                    return True
                return deleted_gc
            except Exception as e:
                db.rollback()
                print(f"Failed to delete booking {booking_id} in SQLite: {e}")
                return False
            finally:
                db.close()
        return deleted_gc


# Initialize calendar service singleton
calendar_service: GoogleCalendarService = GoogleCalendarService(SessionLocal)


def init_calendar_service(db_session_factory=None, calendar_event_model=None) -> GoogleCalendarService:
    """Helper to initialize or refresh the global calendar service singleton."""
    global calendar_service
    if db_session_factory is None:
        db_session_factory = SessionLocal
    calendar_service = GoogleCalendarService(db_session_factory, calendar_event_model=calendar_event_model)
    return calendar_service


# ===========================================================================
# OpenAI Client & Singleton
# ===========================================================================

openai_client: Optional[Any] = None


def init_openai_client() -> Optional[Any]:
    """Initialize or re-initialize the OpenAI client singleton from environment."""
    global openai_client
    if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
        try:
            openai_client = OpenAI()
            print("OpenAI client successfully initialized.")
        except Exception as e:
            print(f"OpenAI client initialization failed: {e}")
            openai_client = None
    else:
        openai_client = None
    return openai_client


# Initialize on module load
init_openai_client()


# ===========================================================================
# Twilio Client & Singleton (Stub for zero-loss backwards compatibility)
# ===========================================================================

twilio_client: Optional[Any] = None


def init_twilio_client() -> Optional[Any]:
    """Initialize or re-initialize the Twilio client singleton if credentials exist."""
    global twilio_client
    if TWILIO_AVAILABLE:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        if account_sid and auth_token:
            try:
                twilio_client = TwilioClient(account_sid, auth_token)
            except Exception as e:
                print(f"Twilio client initialization failed: {e}")
                twilio_client = None
        else:
            twilio_client = None
    else:
        twilio_client = None
    return twilio_client


# Initialize on module load
init_twilio_client()


# ===========================================================================
# GitHub Operations & OIDC Singletons
# ===========================================================================

operations_github_client: OperationsGitHubClient = OperationsGitHubClient()
operations_github_oidc_verifier: GitHubOIDCVerifier = GitHubOIDCVerifier()


__all__ = [
    "GOOGLE_LIBS_AVAILABLE",
    "OPENAI_AVAILABLE",
    "TWILIO_AVAILABLE",
    "GoogleCalendarService",
    "calendar_service",
    "init_calendar_service",
    "openai_client",
    "init_openai_client",
    "twilio_client",
    "init_twilio_client",
    "operations_github_client",
    "operations_github_oidc_verifier",
    "GitHubOIDCError",
    "GitHubOIDCVerifier",
    "OperationsGitHubClient",
    "OperationsGitHubError",
    "redact_sensitive_text",
    "canonical_phone_number",
    "load_line_profiles",
    "save_line_profiles",
    "get_line_profile",
    "resolve_provider_context",
    "_normalize_line_profile",
    "mobilemessage_service",
    "load_business_variables",
    "get_business_variable_values",
    "get_line_business_variable_values",
    "effective_line_user_prompt",
]

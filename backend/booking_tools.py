"""Stable, assistant-facing booking discovery tools.

The language model talks to this small contract instead of knowing whether
availability comes from the legacy calendar or the FastAPI Bookings service.
Booking creation remains behind the separate propose/confirm authorization
boundary in ``main.py``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Protocol
from urllib import error, parse, request
from zoneinfo import ZoneInfo


class BookingToolError(RuntimeError):
    """A safe, expected failure while querying a booking provider."""


class BookingDiscoveryProvider(Protocol):
    def list_services(self) -> list[dict[str, Any]]: ...

    def search_availability(
        self,
        service_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]: ...


class BookingToolSuite:
    """Consistent read-only tools suitable for direct LLM function calling."""

    def __init__(
        self,
        provider: BookingDiscoveryProvider,
        timezone_name: str,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)
        self.now_factory = now_factory or (lambda: datetime.now(self.timezone))

    def current_time(self) -> dict[str, Any]:
        now = self._now()
        return {
            "status": "ok",
            "timezone": self.timezone_name,
            "current_time": now.isoformat(),
            "local_date": now.date().isoformat(),
            "weekday": now.strftime("%A"),
        }

    def services(self) -> dict[str, Any]:
        services = self.provider.list_services()
        return {"status": "ok", "services": services, "count": len(services)}

    def times_today(self, service_id: str, limit: int = 8) -> dict[str, Any]:
        return self._times_for_date(service_id, self._now().date(), limit)

    def times_tomorrow(self, service_id: str, limit: int = 8) -> dict[str, Any]:
        return self._times_for_date(
            service_id,
            self._now().date() + timedelta(days=1),
            limit,
        )

    def next_available(
        self,
        service_id: str,
        after: str | None = None,
        horizon_days: int = 60,
    ) -> dict[str, Any]:
        start = self._parse_after(after)
        end = start + timedelta(days=max(1, min(horizon_days, 180)))
        slots = self.provider.search_availability(service_id, start, end, 1)
        return {
            "status": "ok",
            "timezone": self.timezone_name,
            "service_id": service_id,
            "next_available": slots[0] if slots else None,
        }

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one model tool call and return a customer-safe JSON object."""
        try:
            if tool_name == "get_current_time":
                return self.current_time()
            if tool_name == "list_booking_services":
                return self.services()
            if tool_name == "get_times_today":
                return self.times_today(str(arguments.get("service_id", "")))
            if tool_name == "get_times_tomorrow":
                return self.times_tomorrow(str(arguments.get("service_id", "")))
            if tool_name == "get_next_available":
                return self.next_available(
                    str(arguments.get("service_id", "")),
                    arguments.get("after"),
                )
        except (BookingToolError, OSError, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc)}
        return {"status": "rejected", "reason": f"Unknown booking tool: {tool_name}"}

    def _now(self) -> datetime:
        value = self.now_factory()
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    def _parse_after(self, value: str | None) -> datetime:
        if not value:
            return self._now()
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=self.timezone)
        return max(parsed.astimezone(self.timezone), self._now())

    def _times_for_date(
        self,
        service_id: str,
        local_date: date,
        limit: int,
    ) -> dict[str, Any]:
        day_start = datetime.combine(local_date, time.min, self.timezone)
        day_end = day_start + timedelta(days=1)
        search_start = max(day_start, self._now())
        slots = self.provider.search_availability(
            service_id,
            search_start,
            day_end,
            max(1, min(limit, 24)),
        )
        return {
            "status": "ok",
            "timezone": self.timezone_name,
            "service_id": service_id,
            "date": local_date.isoformat(),
            "slots": slots,
            "count": len(slots),
        }


class LegacyCalendarDiscoveryProvider:
    """Adapter for the application's current Settings + calendar data."""

    def __init__(
        self,
        services_loader: Callable[[], list[dict[str, Any]]],
        working_hours_loader: Callable[[], list[dict[str, Any]]],
        busy_slots_loader: Callable[[datetime, datetime], list[dict[str, datetime]]],
        timezone_name: str,
        slot_interval_minutes: int = 15,
    ) -> None:
        self.services_loader = services_loader
        self.working_hours_loader = working_hours_loader
        self.busy_slots_loader = busy_slots_loader
        self.timezone = ZoneInfo(timezone_name)
        self.slot_interval_minutes = slot_interval_minutes

    def list_services(self) -> list[dict[str, Any]]:
        return [
            {
                "id": str(service.get("id", "")),
                "name": str(service.get("name", "")),
                "description": service.get("description"),
                "duration_minutes": int(service.get("duration", 60)),
                "price": service.get("price"),
            }
            for service in self.services_loader()
            if isinstance(service, dict) and service.get("id") and service.get("name")
        ]

    def search_availability(
        self,
        service_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        service = next(
            (item for item in self.list_services() if item["id"] == service_id),
            None,
        )
        if not service:
            raise BookingToolError("That service is not available.")
        duration = timedelta(minutes=service["duration_minutes"])
        hours = {
            item.get("day"): item
            for item in self.working_hours_loader()
            if isinstance(item, dict) and item.get("day")
        }
        busy = self.busy_slots_loader(start, end)
        cursor = self._round_up(start)
        slots: list[dict[str, Any]] = []
        while cursor < end and len(slots) < limit:
            day = hours.get(cursor.strftime("%A"))
            if day and day.get("enabled"):
                opening = self._at_local_time(cursor, str(day.get("open", "09:00")))
                closing = self._at_local_time(cursor, str(day.get("close", "17:00")))
                candidate = max(cursor, opening)
                candidate = self._round_up(candidate)
                candidate_end = candidate + duration
                if candidate_end <= closing and candidate_end <= end:
                    if not any(
                        candidate < item["end"] and candidate_end > item["start"]
                        for item in busy
                    ):
                        slots.append({
                            "service_id": service["id"],
                            "service_name": service["name"],
                            "start_time": candidate.isoformat(),
                            "end_time": candidate_end.isoformat(),
                        })
            cursor += timedelta(minutes=self.slot_interval_minutes)
        return slots

    def _round_up(self, value: datetime) -> datetime:
        value = value.astimezone(self.timezone)
        interval = self.slot_interval_minutes
        minutes = interval * ((value.minute + interval - 1) // interval)
        rounded = value.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)
        return rounded

    def _at_local_time(self, value: datetime, clock: str) -> datetime:
        hour, minute = (int(part) for part in clock.split(":", 1))
        return value.astimezone(self.timezone).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )


class FastAPIBookingsDiscoveryProvider:
    """HTTP adapter for the sibling FastAPI Bookings application."""

    def __init__(
        self,
        base_url: str,
        tenant: str | None = None,
        token: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("FASTAPI_BOOKINGS_URL is required for the FastAPI booking backend.")
        self.base_url = base_url.rstrip("/")
        self.tenant = tenant
        self.token = token
        self.timeout_seconds = timeout_seconds

    def list_services(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/public/bootstrap")
        services = payload.get("data", {}).get("services", [])
        return [
            {
                "id": str(item["id"]),
                "name": item.get("name", ""),
                "description": item.get("description"),
                "duration_minutes": item.get("duration"),
                "price": item.get("price"),
                "provider_ids": item.get("provider_ids", []),
            }
            for item in services
            if item.get("id") is not None and item.get("active", True)
        ]

    def search_availability(
        self,
        service_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "POST",
            "/api/public/search-availability",
            {
                "service_id": int(service_id),
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
            },
        )
        slots = payload.get("data", [])
        return [
            {
                "service_id": str(item.get("service", {}).get("id", service_id)),
                "service_name": item.get("service", {}).get("name"),
                "provider_id": item.get("provider", {}).get("id"),
                "provider_name": item.get("provider", {}).get("name"),
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
            }
            for item in slots[:limit]
        ]

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.tenant:
            headers["X-Tenant"] = self.tenant
        if self.token:
            headers["X-Token"] = self.token
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode("utf-8")
        req = request.Request(
            parse.urljoin(self.base_url + "/", path.lstrip("/")),
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise BookingToolError(f"Booking service returned HTTP {exc.code}.") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BookingToolError("The booking service is temporarily unavailable.") from exc
        if not payload.get("ok", False):
            raise BookingToolError("The booking service could not complete the availability query.")
        return payload


BOOKING_DISCOVERY_TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_current_time",
        "description": "Return the current local business date and time, including timezone.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "list_booking_services",
        "description": "List the services currently available to book, with exact service IDs, duration and price.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_times_today",
        "description": "Return bookable times remaining today for one exact service ID.",
        "parameters": {
            "type": "object",
            "properties": {"service_id": {"type": "string"}},
            "required": ["service_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_times_tomorrow",
        "description": "Return bookable times tomorrow for one exact service ID.",
        "parameters": {
            "type": "object",
            "properties": {"service_id": {"type": "string"}},
            "required": ["service_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_next_available",
        "description": "Return the next bookable time for one exact service ID, optionally after an ISO 8601 timestamp.",
        "parameters": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string"},
                "after": {"type": ["string", "null"]},
            },
            "required": ["service_id", "after"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

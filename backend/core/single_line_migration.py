"""One-time, idempotent consolidation of legacy two-line application state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.models.domain import ArrivalSession, BlockedContact, CalendarEvent, Message, Note, Thread, ThreadEvent

SMS_ACCOUNT_KEY = "primary"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
    temporary.replace(path)


def _unique_services(*catalogues: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for catalogue in catalogues:
        if not isinstance(catalogue, list):
            continue
        for item in catalogue:
            if not isinstance(item, dict):
                continue
            normalized = {key: value for key, value in item.items() if key not in {"lineKey", "smsAccountKey"}}
            fingerprint = str(normalized.get("id") or "") or json.dumps(normalized, sort_keys=True)
            if fingerprint not in seen:
                seen.add(fingerprint)
                merged.append(normalized)
    return merged


def _service_identity(item: dict[str, Any]) -> str:
    normalized = {key: value for key, value in item.items() if key not in {"lineKey", "smsAccountKey"}}
    return str(normalized.get("id") or "") or json.dumps(normalized, sort_keys=True)


def migrate_file_state(data_dir: str) -> None:
    """Merge legacy line files into the single shared configuration files."""
    directory = Path(data_dir)
    services_path = directory / "services.json"
    primary_services_path = directory / "line_1_services.json"
    secondary_services_path = directory / "line_2_services.json"
    retired_services = _read_json(secondary_services_path, [])
    retired_service_ids = {
        _service_identity(item)
        for item in retired_services
        if isinstance(item, dict)
    }
    # Do not carry forward any retired-line service, even where an older
    # all-services file already contained it.  Only message history is retained.
    consolidated_services = [
        item for item in _unique_services(
            _read_json(services_path, []),
            _read_json(primary_services_path, []),
        )
        if _service_identity(item) not in retired_service_ids
    ]
    if consolidated_services:
        _write_json(services_path, consolidated_services)
    for legacy_path in (primary_services_path, secondary_services_path):
        legacy_path.unlink(missing_ok=True)

    profiles_path = directory / "sms_line_profiles.json"
    profiles = _read_json(profiles_path, {})
    primary_profile = profiles.get(SMS_ACCOUNT_KEY, profiles) if isinstance(profiles, dict) else {}
    if not isinstance(primary_profile, dict):
        primary_profile = {}
    primary_profile.pop("userPrompt", None)
    primary_profile["displayName"] = "SMS"
    _write_json(profiles_path, {SMS_ACCOUNT_KEY: primary_profile})

    responder_path = directory / "first_contact_autoresponder.json"
    responder = _read_json(responder_path, {})
    if isinstance(responder, dict) and isinstance(responder.get("accounts"), dict):
        responder = responder["accounts"].get(SMS_ACCOUNT_KEY, {})
    if isinstance(responder, dict):
        _write_json(responder_path, responder)

    quick_replies_path = directory / "quick_replies.json"
    quick_replies = _read_json(quick_replies_path, [])
    if isinstance(quick_replies, dict):
        accounts = quick_replies.get("accounts", quick_replies)
        quick_replies = accounts.get(SMS_ACCOUNT_KEY, []) if isinstance(accounts, dict) else []
    if isinstance(quick_replies, list):
        _write_json(quick_replies_path, quick_replies)

    gateway_path = directory / "mobilemessage.json"
    gateway = _read_json(gateway_path, {})
    if isinstance(gateway, dict) and isinstance(gateway.get("accounts"), dict):
        gateway = gateway["accounts"].get(SMS_ACCOUNT_KEY, {})
        _write_json(gateway_path, gateway if isinstance(gateway, dict) else {})


def migrate_database_state(db: Session) -> None:
    """Reassign historic second-line records without dropping any conversation data."""
    for legacy in db.query(Thread).filter(Thread.sms_account_key != SMS_ACCOUNT_KEY).all():
        existing = db.query(Thread).filter(
            Thread.sms_account_key == SMS_ACCOUNT_KEY,
            Thread.customer_phone == legacy.customer_phone,
        ).first()
        if existing:
            for model in (Message, Note, ThreadEvent):
                db.query(model).filter(model.thread_id == legacy.id).update(
                    {model.thread_id: existing.id}, synchronize_session=False
                )
            db.query(ArrivalSession).filter(ArrivalSession.thread_id == legacy.id).update(
                {ArrivalSession.thread_id: existing.id}, synchronize_session=False
            )
            db.query(CalendarEvent).filter(CalendarEvent.thread_id == legacy.id).update(
                {CalendarEvent.thread_id: existing.id}, synchronize_session=False
            )
            existing.unread_count = max(existing.unread_count, legacy.unread_count)
            existing.pinned = existing.pinned or legacy.pinned
            if legacy.updated_at > existing.updated_at:
                existing.state = legacy.state
                existing.priority = legacy.priority
                existing.updated_at = legacy.updated_at
            db.delete(legacy)
        else:
            legacy.sms_account_key = SMS_ACCOUNT_KEY

    for legacy in db.query(BlockedContact).filter(BlockedContact.sms_account_key != SMS_ACCOUNT_KEY).all():
        existing = db.query(BlockedContact).filter(
            BlockedContact.sms_account_key == SMS_ACCOUNT_KEY,
            BlockedContact.customer_phone == legacy.customer_phone,
        ).first()
        if existing:
            db.delete(legacy)
        else:
            legacy.sms_account_key = SMS_ACCOUNT_KEY

    db.query(CalendarEvent).filter(CalendarEvent.sms_account_key != SMS_ACCOUNT_KEY).update(
        {CalendarEvent.sms_account_key: SMS_ACCOUNT_KEY}, synchronize_session=False
    )
    db.query(ArrivalSession).filter(ArrivalSession.sms_account_key != SMS_ACCOUNT_KEY).update(
        {ArrivalSession.sms_account_key: SMS_ACCOUNT_KEY}, synchronize_session=False
    )
    db.commit()

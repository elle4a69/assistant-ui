"""Settings, business variables, line profiles, and configurations routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Literal
import uuid

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

try:
    from backend.core.config import (
        BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, WORKING_HOURS_PATH, AVAILABILITY_CONFIG_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, BASE_DIR, DATA_DIR,
        PROMPTS_DIR, MESSAGE_UI_SETTINGS_PATH, DOTENV_AVAILABLE,
        AUTO_REPLY_GLOBAL_ENABLED,
    )
    from backend.core.constants import QUICK_REPLY_DEFAULT_LABELS
    from backend.core.database import get_db
    from backend.core.state import _quick_replies_lock, OUTBOUND_SMS_SEND_LOCK, TRAINING_MODE_ENABLED
    from backend.core.clients import (
        load_line_profiles, save_line_profiles, get_line_profile,
        canonical_phone_number, mobilemessage_service, OPENAI_AVAILABLE,
        OpenAI, openai_client,
    )
    from backend.core.utils import format_dt
    from backend.curator.sanitizer import RESERVED_TEMPLATE_VARIABLES
    from backend.models.domain import BlockedContact, Thread, Message, ThreadEvent
    from backend.schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, LineAutoReplyInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderAccountsInput, BulkSmsSendInput,
        WorkingHoursInput, MobileMessageConfigInput, FirstContactAutoresponderInput,
        AvailabilityConfigInput, AvailabilitySlotDay,
    )
    from backend.services.settings_service import (
        load_business_variables,
        get_business_variable_values,
        load_quick_replies,
        save_quick_replies,
        load_message_ui_settings,
        load_first_contact_autoresponders,
        save_first_contact_autoresponders,
        render_message_export_csv,
    )
    from backend.services.booking_service import load_working_hours
except ImportError:
    from core.config import (
        BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, WORKING_HOURS_PATH, AVAILABILITY_CONFIG_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, BASE_DIR, DATA_DIR,
        PROMPTS_DIR, MESSAGE_UI_SETTINGS_PATH, DOTENV_AVAILABLE,
        AUTO_REPLY_GLOBAL_ENABLED,
    )
    from core.constants import QUICK_REPLY_DEFAULT_LABELS
    from core.database import get_db
    from core.state import _quick_replies_lock, OUTBOUND_SMS_SEND_LOCK, TRAINING_MODE_ENABLED
    from core.clients import (
        load_line_profiles, save_line_profiles, get_line_profile,
        canonical_phone_number, mobilemessage_service, OPENAI_AVAILABLE,
        OpenAI, openai_client,
    )
    from core.utils import format_dt
    from curator.sanitizer import RESERVED_TEMPLATE_VARIABLES
    from models.domain import BlockedContact, Thread, Message, ThreadEvent
    from schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, LineAutoReplyInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderAccountsInput, BulkSmsSendInput,
        WorkingHoursInput, MobileMessageConfigInput, FirstContactAutoresponderInput,
        AvailabilityConfigInput, AvailabilitySlotDay,
    )
    from services.settings_service import (
        load_business_variables,
        get_business_variable_values,
        load_quick_replies,
        save_quick_replies,
        load_message_ui_settings,
        load_first_contact_autoresponders,
        save_first_contact_autoresponders,
        render_message_export_csv,
    )
    from services.booking_service import load_working_hours

import sys


def _dyn(name: str, fallback: Any = None) -> Any:
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


def _set_dyn(name: str, value: Any) -> None:
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            setattr(mod, name, value)


router = APIRouter()

@router.get("/api/settings/blocked-contacts")
def list_blocked_contacts(db: Session = Depends(get_db)):
    contacts = db.query(BlockedContact).order_by(
        BlockedContact.blocked_at.desc(), BlockedContact.id.desc()
    ).all()
    return [{
        "id": contact.id,
        "smsAccountKey": contact.sms_account_key,
        "customerPhone": contact.customer_phone,
        "blockedAt": format_dt(contact.blocked_at),
    } for contact in contacts]


@router.delete("/api/settings/blocked-contacts")
def unblock_contact(
    smsAccountKey: Literal["primary"] = Query("primary"),
    customerPhone: str = Query(...),
    db: Session = Depends(get_db),
):
    canonical_phone = canonical_phone_number(customerPhone)
    contact = db.query(BlockedContact).filter(
        BlockedContact.sms_account_key == smsAccountKey,
        BlockedContact.customer_phone == canonical_phone,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Blocked contact not found")
    db.delete(contact)
    db.commit()
    return {"status": "success", "blocked": False}


@router.post("/api/settings/bulk-sms")
def send_bulk_sms(payload: BulkSmsSendInput, db: Session = Depends(get_db)):
    """Send one owner-confirmed SMS to a validated, de-duplicated list of mobiles.

    Each accepted delivery is recorded in the account-specific conversation so the
    normal inbox and message export remain an accurate history of the campaign.
    A client request ID makes a browser retry safe for each individual recipient.
    """
    recipients: List[str] = []
    invalid_positions: List[int] = []
    seen = set()
    for position, raw_phone in enumerate(payload.recipients, start=1):
        normalized = mobilemessage_service.normalize_sms_destination(raw_phone)
        if not normalized:
            invalid_positions.append(position)
            continue
        customer_phone = canonical_phone_number(normalized)
        if customer_phone and customer_phone not in seen:
            seen.add(customer_phone)
            recipients.append(customer_phone)

    if invalid_positions:
        positions = ", ".join(map(str, invalid_positions))
        raise HTTPException(
            status_code=422,
            detail=f"Invalid Australian mobile number on line or entry: {positions}.",
        )
    if not recipients:
        raise HTTPException(status_code=422, detail="Add at least one valid Australian mobile number.")

    results: List[Dict[str, str]] = []
    for customer_phone in recipients:
        # Keep gateway dispatches serialized, as with a manual conversation
        # reply, but release the lock between recipients so other sends are not
        # held behind an entire campaign.
        with OUTBOUND_SMS_SEND_LOCK:
            now = datetime.utcnow()
            thread = db.query(Thread).filter(
                Thread.sms_account_key == payload.smsAccountKey,
                Thread.customer_phone == customer_phone,
            ).first()
            message_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"assistant-ui:bulk-sms:{payload.clientRequestId}:{payload.smsAccountKey}:{customer_phone}",
            ))
            existing_message = db.query(Message).filter(Message.id == message_id).first()
            if existing_message:
                results.append({"phone": customer_phone, "status": "duplicate"})
                continue

            if not thread:
                thread = Thread(
                    id=str(uuid.uuid4()),
                    customer_phone=customer_phone,
                    sms_account_key=payload.smsAccountKey,
                    state="auto-reply",
                    priority="medium",
                    sla_due_at=now + timedelta(hours=24),
                    unread_count=0,
                    created_at=now,
                    updated_at=now,
                )

            dispatch_result = mobilemessage_service.send_sms(
                customer_phone,
                payload.message,
                idempotency_key=message_id,
                account_key=payload.smsAccountKey,
            )
            delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
            if delivery_failure:
                results.append({
                    "phone": customer_phone,
                    "status": "failed",
                    "error": delivery_failure[:300],
                })
                continue

            try:
                db.add(thread)
                db.add(Message(
                    id=message_id,
                    thread_id=thread.id,
                    role="agent",
                    text=payload.message,
                    provider_message_id=f"bulk-send:{payload.clientRequestId}",
                    at=now,
                ))
                db.add(ThreadEvent(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    type="bulk-sms-sent",
                    agent_id="settings-bulk-sms",
                    meta=json.dumps({"request_id": payload.clientRequestId}),
                    at=now,
                ))
                thread.updated_at = now
                thread.unread_count = 0
                db.commit()
                results.append({"phone": customer_phone, "status": "sent"})
            except Exception:
                db.rollback()
                logger.exception("Bulk SMS was accepted but its local message record could not be saved")
                results.append({
                    "phone": customer_phone,
                    "status": "failed",
                    "error": "SMS was accepted, but its local history could not be saved. Retrying this batch is safe.",
                })

    sent = sum(result["status"] == "sent" for result in results)
    duplicates = sum(result["status"] == "duplicate" for result in results)
    failed = sum(result["status"] == "failed" for result in results)
    return {
        "status": "success" if failed == 0 else "partial",
        "requested": len(recipients),
        "sent": sent,
        "duplicates": duplicates,
        "failed": failed,
        "results": results,
    }


@router.get("/api/settings/business-variables")
def get_business_variables():
    return {"variables": load_business_variables()}


@router.post("/api/settings/business-variables")
def save_business_variables(payload: BusinessVariablesInput):
    normalized = []
    seen = set()
    for item in payload.variables:
        key = item.key.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise HTTPException(
                status_code=422,
                detail=f'Variable "{item.key}" must start with a letter and use only lowercase letters, numbers, and underscores.',
            )
        if key in RESERVED_TEMPLATE_VARIABLES:
            raise HTTPException(status_code=422, detail=f'Variable "{key}" is reserved by the application.')
        if key in seen:
            raise HTTPException(status_code=422, detail=f'Variable "{key}" is duplicated.')
        seen.add(key)
        entry = {
            "key": key,
            "label": item.label.strip(),
            "value": item.value.strip(),
        }
        if item.description is not None:
            entry["description"] = item.description.strip()
        if item.required is not None:
            entry["required"] = bool(item.required)
        normalized.append(entry)
    bv_path = _dyn("BUSINESS_VARIABLES_PATH", BUSINESS_VARIABLES_PATH)
    try:
        os.makedirs(os.path.dirname(bv_path), exist_ok=True)
        with open(bv_path, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, ensure_ascii=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save business variables: {exc}")
    return {"status": "success", "variables": load_business_variables()}


@router.get("/api/settings/line-profiles")
def get_line_profiles():
    return {"profiles": load_line_profiles()}


@router.post("/api/settings/line-profiles")
def update_line_profiles(payload: LineProfilesInput):
    profiles = {
        key: _normalize_line_profile(key, getattr(payload, key).model_dump())
        for key in FIRST_CONTACT_ACCOUNT_KEYS
    }
    for key, profile in profiles.items():
        url = profile["informationUrl"]
        if url and not re.match(r"^https?://", url, flags=re.IGNORECASE):
            raise HTTPException(status_code=422, detail=f"{key} information URL must start with http:// or https://")
    try:
        save_line_profiles(profiles)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to save line profiles.") from exc
    return {"status": "success", "profiles": profiles}


@router.post("/api/settings/line-profiles/{account_key}/auto-reply")
def update_line_auto_reply(
    account_key: Literal["primary"],
    payload: LineAutoReplyInput,
):
    """Persist one line's responder switch without overwriting its other settings."""
    profiles = load_line_profiles()
    profiles[account_key]["autoReplyEnabled"] = payload.enabled
    try:
        save_line_profiles(profiles)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to save the line auto-reply setting.") from exc
    return {
        "status": "success",
        "accountKey": account_key,
        "autoReplyEnabled": payload.enabled,
    }


@router.get("/api/settings/quick-replies/{account_key}")
def get_quick_replies(account_key: Literal["primary"]):
    return {
        "accountKey": account_key,
        "replies": load_quick_replies()[account_key],
    }


@router.put("/api/settings/quick-replies/{account_key}/{slot_index}")
def update_quick_reply(
    account_key: Literal["primary"],
    slot_index: int,
    payload: QuickReplyInput,
):
    if slot_index < 0 or slot_index >= len(QUICK_REPLY_DEFAULT_LABELS):
        raise HTTPException(status_code=404, detail="Quick-reply button not found.")
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="Enter a button label.")
    with _quick_replies_lock:
        replies = load_quick_replies()
        replies[account_key][slot_index] = {
            "label": label,
            "content": payload.content,
        }
        try:
            save_quick_replies(replies)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Failed to save the quick-reply button.") from exc
    return {
        "status": "success",
        "accountKey": account_key,
        "replies": replies[account_key],
    }


@router.get("/api/settings")
def get_settings():
    api_key = os.getenv("OPENAI_API_KEY") or ""
    if api_key:
        if len(api_key) > 12:
            obfuscated_api_key = f"{api_key[:8]}...{api_key[-4:]}"
        else:
            obfuscated_api_key = f"{api_key[:3]}"
    else:
        obfuscated_api_key = ""
        
    prompts_dir = _dyn("PROMPTS_DIR", PROMPTS_DIR)
    base_dir = _dyn("BASE_DIR", BASE_DIR)
    system_prompt_path = os.path.join(prompts_dir, "system_prompt.txt")
    system_prompt_content = "You are a helpful, friendly customer service agent. Use the context and slots."
    if os.path.exists(system_prompt_path):
        try:
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                system_prompt_content = f.read()
        except Exception:
            pass
            
    user_prompt_path = os.path.join(prompts_dir, "user_prompt.txt")
    user_prompt_content = "Customer message: {message}\nKnowledge context:\n{knowledge}\nCalendar openings:\n{slots}"
    if os.path.exists(user_prompt_path):
        try:
            with open(user_prompt_path, "r", encoding="utf-8") as f:
                user_prompt_content = f.read()
        except Exception:
            pass
            
    has_google_credentials = (
        bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")) or
        os.path.exists(os.path.join(base_dir, "service_account.json")) or
        os.path.exists(os.path.join(base_dir, "credentials.json"))
    )
    
    return {
        "openaiApiKey": obfuscated_api_key,
        "systemPrompt": system_prompt_content,
        "userPrompt": user_prompt_content,
        "hasGoogleCredentials": has_google_credentials,
        "autoReplyGlobalEnabled": _dyn("AUTO_REPLY_GLOBAL_ENABLED", AUTO_REPLY_GLOBAL_ENABLED),
        "trainingModeEnabled": _dyn("TRAINING_MODE_ENABLED", TRAINING_MODE_ENABLED),
        "showMessageAvatars": load_message_ui_settings()["showMessageAvatars"],
        "catchUpLookbackDays": load_message_ui_settings()["catchUpLookbackDays"],
    }


@router.post("/api/settings")
def update_settings(payload: SettingsUpdateInput):
    prompts_dir = _dyn("PROMPTS_DIR", PROMPTS_DIR)
    data_dir = _dyn("DATA_DIR", DATA_DIR)
    if payload.systemPrompt is not None:
        system_prompt_path = os.path.join(prompts_dir, "system_prompt.txt")
        os.makedirs(os.path.dirname(system_prompt_path), exist_ok=True)
        with open(system_prompt_path, "w", encoding="utf-8") as f:
            f.write(payload.systemPrompt)

    if payload.userPrompt is not None:
        user_prompt_path = os.path.join(prompts_dir, "user_prompt.txt")
        os.makedirs(os.path.dirname(user_prompt_path), exist_ok=True)
        with open(user_prompt_path, "w", encoding="utf-8") as f:
            f.write(payload.userPrompt)

    if payload.autoReplyGlobalEnabled is not None:
        global AUTO_REPLY_GLOBAL_ENABLED
        AUTO_REPLY_GLOBAL_ENABLED = payload.autoReplyGlobalEnabled
        _set_dyn("AUTO_REPLY_GLOBAL_ENABLED", payload.autoReplyGlobalEnabled)
        auto_reply_path = os.path.join(data_dir, "auto_reply_global.json")
        try:
            os.makedirs(os.path.dirname(auto_reply_path), exist_ok=True)
            with open(auto_reply_path, "w", encoding="utf-8") as f:
                json.dump({"enabled": payload.autoReplyGlobalEnabled}, f, indent=2)
        except Exception as e:
            print(f"Failed to save global auto reply state: {e}")

    if payload.trainingModeEnabled is not None:
        global TRAINING_MODE_ENABLED
        TRAINING_MODE_ENABLED = payload.trainingModeEnabled
        _set_dyn("TRAINING_MODE_ENABLED", payload.trainingModeEnabled)
        training_mode_path = os.path.join(data_dir, "training_mode.json")
        try:
            os.makedirs(os.path.dirname(training_mode_path), exist_ok=True)
            with open(training_mode_path, "w", encoding="utf-8") as f:
                json.dump({"enabled": payload.trainingModeEnabled}, f, indent=2)
        except Exception as e:
            print(f"Failed to save training mode state: {e}")

    if payload.showMessageAvatars is not None or payload.catchUpLookbackDays is not None:
        ui_path = _dyn("MESSAGE_UI_SETTINGS_PATH", MESSAGE_UI_SETTINGS_PATH)
        try:
            os.makedirs(os.path.dirname(ui_path), exist_ok=True)
            message_ui_settings = load_message_ui_settings()
            if payload.showMessageAvatars is not None:
                message_ui_settings["showMessageAvatars"] = payload.showMessageAvatars
            if payload.catchUpLookbackDays is not None:
                message_ui_settings["catchUpLookbackDays"] = payload.catchUpLookbackDays
            with open(ui_path, "w", encoding="utf-8") as handle:
                json.dump(message_ui_settings, handle, indent=2)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to save message UI settings: {exc}")

    if payload.openaiApiKey and "..." not in payload.openaiApiKey:
        env_path = os.path.join(BASE_DIR, ".env")
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        key_found = False
        for i, line in enumerate(lines):
            if line.strip().startswith("OPENAI_API_KEY="):
                lines[i] = f"OPENAI_API_KEY={payload.openaiApiKey}\n"
                key_found = True
                break
        if not key_found:
            lines.append(f"OPENAI_API_KEY={payload.openaiApiKey}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        if DOTENV_AVAILABLE:
            load_dotenv(override=True)

        global openai_client
        if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            try:
                openai_client = OpenAI()
                print("OpenAI client re-initialized.")
            except Exception as e:
                print(f"OpenAI client re-initialization failed: {e}")

    return {"status": "success"}


@router.get("/api/settings/first-contact-autoresponder")
def get_first_contact_autoresponder():
    accounts = load_first_contact_autoresponders()
    return {
        **accounts["primary"],
        "accounts": accounts,
        "labels": {"primary": "SMS"},
    }


@router.post("/api/settings/first-contact-autoresponder")
def save_first_contact_autoresponder(
    payload: FirstContactAutoresponderInput | FirstContactAutoresponderAccountsInput,
):
    if isinstance(payload, FirstContactAutoresponderAccountsInput):
        accounts = {
            key: config.model_dump()
            for key, config in payload.accounts.items()
        }
    else:
        accounts = load_first_contact_autoresponders()
        accounts["primary"] = payload.model_dump()
    try:
        save_first_contact_autoresponders(accounts)
        return {"status": "success", "accounts": load_first_contact_autoresponders()}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save first-contact auto-responder settings: {e}",
        )


@router.get("/api/settings/messages/export.csv")
def export_messages_csv(db: Session = Depends(get_db)):
    try:
        content = render_message_export_csv(db)
    except Exception as exc:
        logger.exception("Message CSV export failed")
        raise HTTPException(status_code=500, detail="Message export could not be generated.") from exc

    filename = f"messages-export-{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/settings/availability")
def get_availability_settings():
    avail_path = AVAILABILITY_CONFIG_PATH
    if os.path.exists(avail_path):
        try:
            with open(avail_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "weeklyHours" in data:
                    return {
                        "maxDaysAhead": int(data.get("maxDaysAhead", 14)),
                        "weeklyHours": data["weeklyHours"],
                    }
        except Exception as exc:
            logger.warning("Could not read availability config: %s", exc)

    # If missing, initialize from working_hours.json by expanding open..close into 30-minute slots
    wh = load_working_hours()
    weekly_hours = []
    for entry in wh:
        day = entry.get("day", "")
        enabled = entry.get("enabled", True)
        open_time = entry.get("open", "09:00")
        close_time = entry.get("close", "17:00")
        slots = []
        if enabled and open_time and close_time:
            try:
                oh, om = map(int, open_time.split(":"))
                ch, cm = map(int, close_time.split(":"))
                curr = oh * 60 + om
                end = ch * 60 + cm
                while curr + 30 <= end:
                    slots.append(f"{curr // 60:02d}:{curr % 60:02d}")
                    curr += 30
            except Exception:
                slots = []
        weekly_hours.append({
            "day": day,
            "enabled": enabled,
            "open": open_time,
            "close": close_time,
            "slots": slots,
        })

    config = {
        "maxDaysAhead": 14,
        "weeklyHours": weekly_hours,
    }
    try:
        os.makedirs(os.path.dirname(avail_path), exist_ok=True)
        with open(avail_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as exc:
        logger.warning("Could not initialize availability config: %s", exc)

    return config


@router.post("/api/settings/availability")
def save_availability_settings(payload: AvailabilityConfigInput):
    avail_path = AVAILABILITY_CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(avail_path), exist_ok=True)
        config_data = payload.model_dump()
        with open(avail_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        # Update working_hours.json for backwards compatibility
        wh_entries = []
        for day_cfg in payload.weeklyHours:
            if day_cfg.enabled and day_cfg.slots:
                sorted_slots = sorted(day_cfg.slots, key=lambda s: tuple(map(int, s.split(":"))))
                open_str = sorted_slots[0]
                last_slot = sorted_slots[-1]
                lh, lm = map(int, last_slot.split(":"))
                close_m = lh * 60 + lm + 30
                close_str = f"{close_m // 60:02d}:{close_m % 60:02d}"
            else:
                open_str = day_cfg.open or "09:00"
                close_str = day_cfg.close or "17:00"
            wh_entries.append({
                "day": day_cfg.day,
                "enabled": day_cfg.enabled,
                "open": open_str,
                "close": close_str,
            })

        os.makedirs(os.path.dirname(WORKING_HOURS_PATH), exist_ok=True)
        with open(WORKING_HOURS_PATH, "w", encoding="utf-8") as f:
            json.dump(wh_entries, f, indent=2)

        return {
            "status": "success",
            "maxDaysAhead": payload.maxDaysAhead,
            "weeklyHours": [day.model_dump() for day in payload.weeklyHours],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save availability settings: {e}")

@router.get("/api/settings/working-hours")
def get_working_hours():
    return load_working_hours()


@router.post("/api/settings/working-hours")
def save_working_hours(payload: WorkingHoursInput):
    try:
        os.makedirs(os.path.dirname(WORKING_HOURS_PATH), exist_ok=True)
        data = [entry.model_dump() for entry in payload.hours]
        with open(WORKING_HOURS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Synchronize availability_config.json if it exists (or create it),
        # updating each day's open, close, enabled, and expanding open..close
        # into 30-minute interval slots for that day, preserving maxDaysAhead.
        max_days_ahead = 14
        avail_path = AVAILABILITY_CONFIG_PATH
        if os.path.exists(avail_path):
            try:
                with open(avail_path, "r", encoding="utf-8") as f:
                    existing_cfg = json.load(f)
                    if isinstance(existing_cfg, dict):
                        max_days_ahead = int(existing_cfg.get("maxDaysAhead", 14))
            except Exception as exc:
                logger.warning("Could not read existing availability config: %s", exc)

        weekly_hours = []
        for entry in data:
            day = entry.get("day", "")
            enabled = bool(entry.get("enabled", True))
            open_time = entry.get("open", "09:00")
            close_time = entry.get("close", "17:00")
            slots = []
            if enabled and open_time and close_time:
                try:
                    oh, om = map(int, open_time.split(":"))
                    ch, cm = map(int, close_time.split(":"))
                    curr = oh * 60 + om
                    end = ch * 60 + cm
                    while curr + 30 <= end:
                        slots.append(f"{curr // 60:02d}:{curr % 60:02d}")
                        curr += 30
                except Exception:
                    slots = []
            weekly_hours.append({
                "day": day,
                "enabled": enabled,
                "open": open_time,
                "close": close_time,
                "slots": slots,
            })

        avail_config = {
            "maxDaysAhead": max_days_ahead,
            "weeklyHours": weekly_hours,
        }
        os.makedirs(os.path.dirname(avail_path), exist_ok=True)
        with open(avail_path, "w", encoding="utf-8") as f:
            json.dump(avail_config, f, indent=2)

        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save working hours: {e}")


@router.get("/api/settings/mobilemessage")
def get_mobilemessage_settings():
    config = mobilemessage_service.load_config()
    return {
        "username": config.get("username", ""),
        "password": "",
        "hasPassword": bool(config.get("password")),
        "sender": config.get("sender", ""),
        "enabled": bool(config.get("enabled", False)),
    }


@router.post("/api/settings/mobilemessage")
def save_mobilemessage_settings(payload: MobileMessageConfigInput):
    config = payload.model_dump()
    if not config.get("password"):
        config["password"] = mobilemessage_service.load_config().get("password", "")
    config["enabled"] = bool(config.get("username") and config.get("password") and payload.enabled)
    success = mobilemessage_service.save_config(config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save MobileMessage configuration.")
    return {"status": "success"}


__all__ = [
    "router",
    "list_blocked_contacts",
    "unblock_contact",
    "send_bulk_sms",
    "get_business_variables",
    "save_business_variables",
    "get_line_profiles",
    "update_line_profiles",
    "update_line_auto_reply",
    "get_quick_replies",
    "update_quick_reply",
    "get_settings",
    "update_settings",
    "get_first_contact_autoresponder",
    "save_first_contact_autoresponder",
    "export_messages_csv",
    "get_working_hours",
    "save_working_hours",
    "get_availability_settings",
    "save_availability_settings",
    "get_mobilemessage_settings",
    "save_mobilemessage_settings",
]

"""Settings, business variables, line profiles, and configurations routes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Literal

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
        FIRST_CONTACT_AUTORESPONDER_PATH, WORKING_HOURS_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, BASE_DIR, DATA_DIR,
        PROMPTS_DIR, MESSAGE_UI_SETTINGS_PATH, DOTENV_AVAILABLE,
        AUTO_REPLY_GLOBAL_ENABLED,
    )
    from backend.core.constants import QUICK_REPLY_DEFAULT_LABELS
    from backend.core.database import get_db
    from backend.core.state import _quick_replies_lock, TRAINING_MODE_ENABLED
    from backend.core.clients import (
        load_line_profiles, save_line_profiles, get_line_profile,
        canonical_phone_number, mobilemessage_service, OPENAI_AVAILABLE,
        OpenAI, openai_client,
    )
    from backend.core.utils import format_dt
    from backend.curator.sanitizer import RESERVED_TEMPLATE_VARIABLES
    from backend.models.domain import BlockedContact, Thread, Message
    from backend.schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderAccountsInput,
        WorkingHoursInput, MobileMessageConfigInput, FirstContactAutoresponderInput,
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
        FIRST_CONTACT_AUTORESPONDER_PATH, WORKING_HOURS_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, BASE_DIR, DATA_DIR,
        PROMPTS_DIR, MESSAGE_UI_SETTINGS_PATH, DOTENV_AVAILABLE,
        AUTO_REPLY_GLOBAL_ENABLED,
    )
    from core.constants import QUICK_REPLY_DEFAULT_LABELS
    from core.database import get_db
    from core.state import _quick_replies_lock, TRAINING_MODE_ENABLED
    from core.clients import (
        load_line_profiles, save_line_profiles, get_line_profile,
        canonical_phone_number, mobilemessage_service, OPENAI_AVAILABLE,
        OpenAI, openai_client,
    )
    from core.utils import format_dt
    from curator.sanitizer import RESERVED_TEMPLATE_VARIABLES
    from models.domain import BlockedContact, Thread, Message
    from schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderAccountsInput,
        WorkingHoursInput, MobileMessageConfigInput, FirstContactAutoresponderInput,
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
    smsAccountKey: Literal["primary", "secondary"] = Query(...),
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


@router.get("/api/settings/quick-replies/{account_key}")
def get_quick_replies(account_key: Literal["primary", "secondary"]):
    return {
        "accountKey": account_key,
        "replies": load_quick_replies()[account_key],
    }


@router.put("/api/settings/quick-replies/{account_key}/{slot_index}")
def update_quick_reply(
    account_key: Literal["primary", "secondary"],
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
        "labels": {"primary": "Line 1", "secondary": "Line 2"},
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
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save working hours: {e}")


@router.get("/api/settings/mobilemessage")
def get_mobilemessage_settings():
    accounts = mobilemessage_service.load_accounts_config()
    config = mobilemessage_service.load_config()
    accounts["primary"] = config
    public_accounts = {}
    for key, account in accounts.items():
        public_accounts[key] = {
            "username": account.get("username", ""),
            "password": "",
            "hasPassword": bool(account.get("password")),
            "sender": account.get("sender", ""),
            "enabled": bool(account.get("enabled", False)),
        }
    return {
        "username": config.get("username", ""),
        "password": "",
        "hasPassword": bool(config.get("password")),
        "sender": config.get("sender", ""),
        "enabled": bool(config.get("enabled", False)),
        "accounts": public_accounts,
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
    "get_business_variables",
    "save_business_variables",
    "get_line_profiles",
    "update_line_profiles",
    "get_quick_replies",
    "update_quick_reply",
    "get_settings",
    "update_settings",
    "get_first_contact_autoresponder",
    "save_first_contact_autoresponder",
    "export_messages_csv",
    "get_working_hours",
    "save_working_hours",
    "get_mobilemessage_settings",
    "save_mobilemessage_settings",
]

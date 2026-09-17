"""Settings, catalogue, business variables, and configuration service."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        BASE_DIR, DATA_DIR, BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        MESSAGE_UI_SETTINGS_PATH, QUICK_REPLIES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, FIRST_CONTACT_ACCOUNT_KEYS,
        QUICK_REPLY_ACCOUNT_KEYS, QUICK_REPLY_DEFAULT_LABELS,
        MESSAGE_EXPORT_COLUMNS, FIRST_CONTACT_AUTORESPONDER_DEFAULT,
        BUSINESS_VARIABLE_DEFAULTS, CONVERSATIONAL_AI_ACCOUNT_KEYS,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS,
    )
    from backend.core.constants import LINE_SERVICE_FILENAMES
    from backend.core.state import _quick_replies_lock
    from backend.core.utils import _safe_csv_cell, format_dt
    from backend.models.domain import Thread, Message, BlockedContact
    from backend.core.clients import (
        load_line_profiles, canonical_phone_number, get_line_profile,
        resolve_provider_context,
    )
    from backend.curator.authority import _knowledge_reason
except ImportError:
    from core.config import (
        BASE_DIR, DATA_DIR, BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        MESSAGE_UI_SETTINGS_PATH, QUICK_REPLIES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, FIRST_CONTACT_ACCOUNT_KEYS,
        QUICK_REPLY_ACCOUNT_KEYS, QUICK_REPLY_DEFAULT_LABELS,
        MESSAGE_EXPORT_COLUMNS, FIRST_CONTACT_AUTORESPONDER_DEFAULT,
        BUSINESS_VARIABLE_DEFAULTS, CONVERSATIONAL_AI_ACCOUNT_KEYS,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS,
    )
    from core.constants import LINE_SERVICE_FILENAMES
    from core.state import _quick_replies_lock
    from core.utils import _safe_csv_cell, format_dt
    from models.domain import Thread, Message, BlockedContact
    from core.clients import (
        load_line_profiles, canonical_phone_number, get_line_profile,
        resolve_provider_context,
    )
    from curator.authority import _knowledge_reason

logger = logging.getLogger(__name__)


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


def _line_services_path(account_key: str) -> str:
    data_dir = _dyn("DATA_DIR", DATA_DIR)
    return os.path.join(data_dir, LINE_SERVICE_FILENAMES[account_key])


def _service_line_key(service: Dict[str, Any]) -> str:
    explicit = str(service.get("lineKey") or service.get("smsAccountKey") or "").strip().lower()
    if explicit in FIRST_CONTACT_ACCOUNT_KEYS:
        return explicit
    return "secondary" if "anonymous" in str(service.get("name", "")).lower() else "primary"


def _read_service_catalogue(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _ensure_line_service_catalogues() -> None:
    if all(os.path.exists(_line_services_path(key)) for key in FIRST_CONTACT_ACCOUNT_KEYS):
        return
    data_dir = _dyn("DATA_DIR", DATA_DIR)
    legacy = _read_service_catalogue(os.path.join(data_dir, "services.json"))
    if not legacy:
        return
    grouped = {key: [] for key in FIRST_CONTACT_ACCOUNT_KEYS}
    for service in legacy:
        key = _service_line_key(service)
        grouped[key].append({**service, "lineKey": key})
    os.makedirs(data_dir, exist_ok=True)
    for key, services in grouped.items():
        path = _line_services_path(key)
        if not os.path.exists(path):
            temporary = f"{path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(services, handle, indent=2)
            os.replace(temporary, path)


def load_line_services(account_key: str) -> List[Dict[str, Any]]:
    if account_key not in FIRST_CONTACT_ACCOUNT_KEYS:
        return []
    _ensure_line_service_catalogues()
    return _read_service_catalogue(_line_services_path(account_key))


def load_all_line_services() -> List[Dict[str, Any]]:
    return [service for key in FIRST_CONTACT_ACCOUNT_KEYS for service in load_line_services(key)]


def get_live_services_context(account_key: str = "primary") -> str:
    """Read the current Settings service catalogue for every AI reply.

    This intentionally avoids a cache: saving Settings should affect the very next
    conversation without a restart or a separate knowledge-base upload.
    """
    services = load_line_services(account_key)
    if not services:
        return ""

    rendered = ["[Live services and prices from Settings]"]
    for service in services:
        if not isinstance(service, dict):
            continue
        name = str(service.get("name", "")).strip()
        if not name:
            continue
        details = [name]
        service_id = str(service.get("id", "")).strip()
        if service_id:
            details.append(f"Booking service ID: {service_id}")
        price = service.get("price")
        if price is not None:
            details.append(f"Price: ${price}")
        duration = service.get("duration")
        if duration is not None and service.get("showDuration", True) is not False:
            details.append(f"Duration: {duration} minutes")
        description = re.sub(
            r"\s+", " ", str(service.get("description", "")).strip()
        )
        if description:
            details.append(f"Description: {description}")
        rendered.append("\n".join(details))
    return "\n\n".join(rendered) if len(rendered) > 1 else ""


def load_business_variables() -> List[Dict[str, Any]]:
    bv_path = _dyn("BUSINESS_VARIABLES_PATH", BUSINESS_VARIABLES_PATH)
    default_meta = {d["key"]: d for d in BUSINESS_VARIABLE_DEFAULTS}
    if not os.path.exists(bv_path):
        raw_items = [dict(item) for item in BUSINESS_VARIABLE_DEFAULTS]
    else:
        try:
            with open(bv_path, "r", encoding="utf-8") as handle:
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
    bv_path = _dyn("BUSINESS_VARIABLES_PATH", BUSINESS_VARIABLES_PATH)
    raw_vals = {
        item["key"]: item["value"]
        for item in load_business_variables()
        if item.get("value", "").strip()
    }
    website_val = raw_vals.get("website", "").strip() or raw_vals.get("booking_url", "").strip()
    if website_val:
        raw_vals["website"] = website_val
        raw_vals["booking_url"] = website_val

    if not os.path.exists(bv_path) and not raw_vals:
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
    fn = _dyn("get_line_business_variable_values", None)
    if callable(fn) and fn is not get_line_business_variable_values:
        return fn(account_key)
    try:
        from backend.core.clients import get_line_business_variable_values as _clients_glbvv
        return _clients_glbvv(account_key)
    except ImportError:
        from core.clients import get_line_business_variable_values as _clients_glbvv
        return _clients_glbvv(account_key)


def effective_line_user_prompt(account_key: str, shared_prompt: str) -> str:
    """Use an explicitly saved line prompt, falling back to the shared template."""
    fn = _dyn("effective_line_user_prompt", None)
    if callable(fn) and fn is not effective_line_user_prompt:
        return fn(account_key, shared_prompt)
    try:
        from backend.core.clients import effective_line_user_prompt as _clients_elup
        return _clients_elup(account_key, shared_prompt)
    except ImportError:
        from core.clients import effective_line_user_prompt as _clients_elup
        return _clients_elup(account_key, shared_prompt)


def get_live_business_variables_context() -> str:
    variables = [item for item in load_business_variables() if item.get("value", "").strip()]
    if not variables:
        return ""
    rendered = ["[Authoritative business details from Settings]"]
    rendered.extend(f"{item['label']}: {item['value']}" for item in variables)
    return "\n".join(rendered)


def build_business_context(query: str, limit: int = 3, account_key: str = "primary") -> str:
    """Combine optional uploaded knowledge with authoritative live Settings."""
    try:
        from backend.services.knowledge_service import retrieve_knowledge_chunks, resolve_knowledge_template
    except ImportError:
        from services.knowledge_service import retrieve_knowledge_chunks, resolve_knowledge_template
    resolve_provider_context(account_key)
    output_parts = []
    matched_chunks = retrieve_knowledge_chunks(query, limit=limit, account_key=account_key)
    for result in matched_chunks:
        if result.get("type", "text") == "text":
            # Current prices, durations and availability are never learned
            # authorities. They must come from Settings or the live calendar.
            # This also protects legacy uploaded records that predate the
            # classification field.
            if re.search(
                r"(?:\$\s*\d|\b\d+\s*(?:minutes?|mins?|hours?|hrs?)\b|\b(?:available|availability|free|opening|slot|booked\s+out)\b)",
                str(result.get("text") or ""), re.IGNORECASE,
            ):
                _knowledge_reason("knowledge_authority_overridden", str(result.get("id") or ""))
                continue
            # Learning templates may contain line/profile and semantic service
            # variables. Render them only for the receiving line before they
            # enter the model context; never leave historical template tokens
            # for the model to guess at.
            rendered = resolve_knowledge_template(str(result["text"]), account_key)
            if rendered is None:
                _knowledge_reason("knowledge_excluded_unresolved_template", str(result.get("id") or ""))
                continue
            output_parts.append(f"[Source: {result['source']}]\n{rendered}")

    fn_services = _dyn("get_live_services_context", get_live_services_context)
    services_context = fn_services(account_key)
    if services_context:
        output_parts.append(services_context)
    return "\n\n".join(output_parts) or "No relevant business records found."


def account_allows_conversational_ai(account_key: str) -> bool:
    """Allow conversational AI only for explicitly configured SMS accounts."""
    return account_key in CONVERSATIONAL_AI_ACCOUNT_KEYS


def normalize_first_contact_autoresponder(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(FIRST_CONTACT_AUTORESPONDER_DEFAULT)
    normalized.update(config)
    try:
        normalized["cooldownDays"] = max(1, min(3650, int(normalized.get("cooldownDays", 30))))
    except (TypeError, ValueError):
        normalized["cooldownDays"] = 30
    try:
        normalized["delaySeconds"] = max(0, min(3600, int(normalized.get("delaySeconds", 0))))
    except (TypeError, ValueError):
        normalized["delaySeconds"] = 0
    normalized["enabled"] = bool(normalized.get("enabled", False))
    normalized["message"] = str(normalized.get("message", "")).strip()
    return normalized


def load_first_contact_autoresponders() -> Dict[str, Dict[str, Any]]:
    ar_path = _dyn("FIRST_CONTACT_AUTORESPONDER_PATH", FIRST_CONTACT_AUTORESPONDER_PATH)
    accounts = {
        key: dict(FIRST_CONTACT_AUTORESPONDER_DEFAULT)
        for key in FIRST_CONTACT_ACCOUNT_KEYS
    }
    saved: Dict[str, Any] = {}
    if os.path.exists(ar_path):
        try:
            with open(ar_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                saved = loaded
        except Exception as e:
            print(f"Failed to read first-contact auto-responder settings: {e}")

    if isinstance(saved.get("accounts"), dict):
        for key in FIRST_CONTACT_ACCOUNT_KEYS:
            if isinstance(saved["accounts"].get(key), dict):
                accounts[key].update(saved["accounts"][key])
    elif saved:
        # The original single responder belongs to the original Tori account.
        accounts["primary"].update(saved)

    return {
        key: normalize_first_contact_autoresponder(config)
        for key, config in accounts.items()
    }


def load_first_contact_autoresponder(account_key: str = "primary") -> Dict[str, Any]:
    accounts = load_first_contact_autoresponders()
    return accounts.get(account_key, accounts["primary"])


def save_first_contact_autoresponders(accounts: Dict[str, Dict[str, Any]]) -> None:
    ar_path = _dyn("FIRST_CONTACT_AUTORESPONDER_PATH", FIRST_CONTACT_AUTORESPONDER_PATH)
    normalized = {
        key: normalize_first_contact_autoresponder(accounts.get(key, {}))
        for key in FIRST_CONTACT_ACCOUNT_KEYS
    }
    os.makedirs(os.path.dirname(ar_path), exist_ok=True)
    temp_path = f"{ar_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"accounts": normalized}, f, indent=2)
    os.replace(temp_path, ar_path)


def load_message_ui_settings() -> Dict[str, Any]:
    ui_path = _dyn("MESSAGE_UI_SETTINGS_PATH", MESSAGE_UI_SETTINGS_PATH)
    defaults = {
        "showMessageAvatars": True,
        "catchUpLookbackDays": DEFAULT_CATCH_UP_LOOKBACK_DAYS,
    }
    if not os.path.exists(ui_path):
        return defaults
    try:
        with open(ui_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict):
            try:
                lookback_days = int(saved.get("catchUpLookbackDays", DEFAULT_CATCH_UP_LOOKBACK_DAYS))
            except (TypeError, ValueError):
                lookback_days = DEFAULT_CATCH_UP_LOOKBACK_DAYS
            return {
                "showMessageAvatars": bool(saved.get("showMessageAvatars", True)),
                "catchUpLookbackDays": min(30, max(1, lookback_days)),
            }
    except Exception:
        pass
    return defaults


def default_quick_replies() -> Dict[str, List[Dict[str, str]]]:
    return {
        account_key: [
            {"label": label, "content": ""}
            for label in QUICK_REPLY_DEFAULT_LABELS
        ]
        for account_key in QUICK_REPLY_ACCOUNT_KEYS
    }


def load_quick_replies() -> Dict[str, List[Dict[str, str]]]:
    qr_path = _dyn("QUICK_REPLIES_PATH", QUICK_REPLIES_PATH)
    defaults = default_quick_replies()
    if not os.path.exists(qr_path):
        return defaults
    try:
        with open(qr_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        accounts = saved.get("accounts", saved) if isinstance(saved, dict) else {}
        normalized: Dict[str, List[Dict[str, str]]] = {}
        for account_key in QUICK_REPLY_ACCOUNT_KEYS:
            account_items = accounts.get(account_key, []) if isinstance(accounts, dict) else []
            replies = []
            for index, fallback in enumerate(defaults[account_key]):
                item = account_items[index] if isinstance(account_items, list) and index < len(account_items) else {}
                label = str(item.get("label") or fallback["label"]).strip()[:8] if isinstance(item, dict) else fallback["label"]
                content = str(item.get("content") or "")[:4000] if isinstance(item, dict) else ""
                replies.append({"label": label or fallback["label"], "content": content})
            normalized[account_key] = replies
        return normalized
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return defaults


def save_quick_replies(replies: Dict[str, List[Dict[str, str]]]) -> None:
    qr_path = _dyn("QUICK_REPLIES_PATH", QUICK_REPLIES_PATH)
    os.makedirs(os.path.dirname(qr_path), exist_ok=True)
    temporary_path = f"{qr_path}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump({"accounts": replies}, handle, indent=2, ensure_ascii=False)
    os.replace(temporary_path, qr_path)


def render_message_export_csv(db: Session) -> str:
    rows = (
        db.query(Message, Thread)
        .join(Thread, Thread.id == Message.thread_id)
        .order_by(Message.at.asc(), Message.id.asc())
        .all()
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(MESSAGE_EXPORT_COLUMNS)
    for message, thread in rows:
        direction = {
            "customer": "inbound",
            "agent": "outbound",
            "draft": "draft",
            "system": "system",
        }.get(message.role, message.role)
        writer.writerow(
            _safe_csv_cell(value)
            for value in (
                thread.sms_account_key,
                format_dt(message.at),
                direction,
                message.text,
                thread.id,
                thread.customer_phone,
            )
        )
    return output.getvalue()


__all__ = [
    "_line_services_path",
    "_service_line_key",
    "_read_service_catalogue",
    "_ensure_line_service_catalogues",
    "load_line_services",
    "load_all_line_services",
    "get_live_services_context",
    "load_business_variables",
    "get_business_variable_values",
    "get_line_business_variable_values",
    "effective_line_user_prompt",
    "get_live_business_variables_context",
    "build_business_context",
    "account_allows_conversational_ai",
    "normalize_first_contact_autoresponder",
    "load_first_contact_autoresponders",
    "load_first_contact_autoresponder",
    "save_first_contact_autoresponders",
    "load_message_ui_settings",
    "default_quick_replies",
    "load_quick_replies",
    "save_quick_replies",
    "render_message_export_csv",
]

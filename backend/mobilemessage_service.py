import os
import json
import requests
from requests.auth import HTTPBasicAuth
from typing import Optional, Dict, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSIST_DIR = "/data" if os.path.exists("/data") else BASE_DIR
CONFIG_PATH = os.path.join(PERSIST_DIR, "data", "mobilemessage.json")
API_BASE_URL = "https://api.mobilemessage.com.au/v1"
_resolved_sender: Optional[str] = None

def load_config() -> Dict[str, Any]:
    config = {
        "username": os.getenv("MOBILEMESSAGE_USERNAME") or os.getenv("SMS_API_KEY", ""),
        "password": os.getenv("MOBILEMESSAGE_PASSWORD") or os.getenv("SMS_API_SECRET", ""),
        "sender": os.getenv("MOBILEMESSAGE_SENDER") or os.getenv("SMS_SENDER_NUMBER", ""),
        "enabled": True
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            print(f"Error loading mobilemessage.json: {e}")
            
    # Auto-enable if credentials exist
    if config["username"] and config["password"]:
        config["enabled"] = True
        
    return config

def save_config(new_config: Dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving mobilemessage.json: {e}")
        return False

def _resolve_sender(username: str, password: str, configured_sender: str) -> Optional[str]:
    global _resolved_sender
    if configured_sender:
        return configured_sender
    if _resolved_sender:
        return _resolved_sender
    try:
        response = requests.get(
            f"{API_BASE_URL}/senders",
            auth=HTTPBasicAuth(username, password),
            timeout=10,
        )
        if not response.ok:
            print(f"MobileMessage sender lookup failed ({response.status_code}).")
            return None
        senders = response.json().get("results", [])
        selected = next((item for item in senders if item.get("is_default")), None)
        selected = selected or (senders[0] if senders else None)
        _resolved_sender = selected.get("sender") if selected else None
        return _resolved_sender
    except Exception as exc:
        print(f"MobileMessage sender lookup exception: {type(exc).__name__}")
        return None


def send_sms(
    to_phone: str,
    message: str,
    custom_sender: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = load_config()
    username = cfg.get("username", "").strip()
    password = cfg.get("password", "").strip()
    enabled = cfg.get("enabled", True)
    
    if not username or not password:
        print("MobileMessage dispatch skipped (credentials missing).")
        return {"status": "skipped", "reason": "Not configured"}
    if not enabled:
        print("MobileMessage dispatch skipped (gateway disabled).")
        return {"status": "skipped", "reason": "Gateway disabled"}

    sender_id = _resolve_sender(
        username,
        password,
        (custom_sender or cfg.get("sender", "")).strip(),
    )
    if not sender_id:
        print("MobileMessage dispatch failed (no registered sender available).")
        return {"status": "error", "reason": "No registered sender available"}
    
    # Format destination phone to standard international
    clean_to = to_phone.strip().lstrip("+")
    
    payload_msg: Dict[str, Any] = {
        "to": clean_to,
        "message": message
    }
    if sender_id:
        payload_msg["sender"] = sender_id

    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    try:
        resp = requests.post(
            f"{API_BASE_URL}/messages",
            json={"messages": [payload_msg]},
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=10
        )
        if resp.ok:
            data = resp.json()
            results = data.get("results", [])
            accepted = bool(results) and all(item.get("status") == "success" for item in results)
            if accepted:
                print(f"MobileMessage accepted SMS for {clean_to}.")
                return {"status": "success", "data": data}
            detail = json.dumps(data, ensure_ascii=True)
            print(f"MobileMessage rejected SMS in response body: {detail}")
            return {"status": "error", "code": resp.status_code, "detail": detail}
        else:
            print(f"MobileMessage API error ({resp.status_code}): {resp.text}")
            return {"status": "error", "code": resp.status_code, "detail": resp.text}
    except Exception as e:
        print(f"MobileMessage request exception: {e}")
        return {"status": "exception", "detail": str(e)}


def delivery_error(result: Dict[str, Any]) -> Optional[str]:
    """Return a safe operator-facing error when the gateway did not accept the SMS."""
    if result.get("status") == "success":
        return None
    code = result.get("code")
    reason = result.get("reason") or result.get("detail") or "Unknown gateway error"
    prefix = f"MobileMessage HTTP {code}" if code else "MobileMessage"
    return f"{prefix}: {reason}"

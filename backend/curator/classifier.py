"""Knowledge Curator candidate classification and learned knowledge scoping."""

from datetime import datetime
import json
import os
import re
import threading
from typing import Any, Dict, List, Optional
import uuid

from .authority import (
    LEARNED_INFORMATION_FILENAME,
    _knowledge_bool,
    resolve_knowledge_authority,
)
from .compat import DEFAULT_LEARNED_INFORMATION_LOCK, get_main_attr
from .sanitizer import (
    has_unsafe_literal_learning_detail,
    sanitise_reusable_knowledge_template,
)
from .service import _curator_records, _parse_json_object

_DEFAULT_KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")

def _knowledge_meaning_signature(text: str) -> str:
    value = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").casefold())
    value = " ".join(value.split())
    if re.search(r"(?:what|which) day .*?(?:suit|thinking)|when would you like .*?(?:come|book)|preferred appointment day", value):
        return "ask customer preferred appointment day"
    replacements = {
        "appointment": "booking", "appointments": "booking", "suits": "preferred",
        "suit": "preferred", "thinking": "preferred", "come": "book",
    }
    return " ".join(replacements.get(token, token) for token in value.split())


def classify_knowledge_candidate(entry: Dict[str, Any]) -> str:
    """Classify a sanitised candidate before it can become a draft or record."""
    account_key = str(entry.get("scope") or entry.get("sms_account_key") or "")
    account_keys = get_main_attr("FIRST_CONTACT_ACCOUNT_KEYS", ("primary", "secondary"))
    if account_key not in {*account_keys, "shared"}:
        return "uncertain"
    candidate_text = str(entry.get("text") or entry.get("instruction") or "")
    signature = _knowledge_meaning_signature(candidate_text)
    if not signature:
        return "uncertain"
    if account_key == "shared":
        applicable = [item for item in _curator_records() if item.get("sms_account_key") == "shared"
                      and item.get("status") == "active" and item.get("review_status") == "approved"
                      and _knowledge_bool(item.get("retrieval_enabled"))]
    else:
        active = resolve_knowledge_authority(_curator_records(), account_key=account_key)
        applicable = [item for item in active if item.get("sms_account_key") in {account_key, "shared"}]
    if re.search(r"\b(?:must not|never|instead|except)\b", candidate_text, re.IGNORECASE):
        return "conflict" if any(item.get("canonical_key") == entry.get("canonical_key") for item in applicable) else "genuinely_new"
    for item in applicable:
        if _knowledge_meaning_signature(str(item.get("text") or "")) == signature:
            return "duplicate"
    same_key = [item for item in applicable if item.get("canonical_key") == entry.get("canonical_key")]
    if not same_key:
        return "genuinely_new"
    if entry.get("proposed_supersedes_id") or re.search(r"\b(?:replace|supersede)\b", candidate_text, re.IGNORECASE):
        return "replacement"
    if any(re.search(r"\b(?:must not|never|instead|except)\b", candidate_text, re.IGNORECASE) for _ in same_key):
        return "conflict"
    return "material_addition"


def prepare_learning_candidate(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Sanitise and deduplicate a candidate without retaining rejected content."""
    prepared = dict(entry)
    # Source messages remain in the normal account-bound message history. A
    # reusable draft stores only its bounded template and source identifier,
    # never a copied customer question, reply, name, phone, or owner payload.
    for private_field in (
        "question", "owner_information", "customer_message", "customer",
        "reply", "approved_reply", "owner_guidance",
    ):
        prepared.pop(private_field, None)
    account_key = str(prepared.get("scope") or prepared.get("sms_account_key") or "")
    rendered_fields = {}
    for field in ("topic", "applies_when", "instruction", "example_reply", "text"):
        if field in prepared and prepared.get(field):
            value = sanitise_reusable_knowledge_template(str(prepared[field]), account_key)
            if value is None:
                return None
            rendered_fields[field] = value
    prepared.update(rendered_fields)
    if prepared.get("instruction"):
        parts = [f"Topic: {prepared.get('topic', '')}", f"Applies when: {prepared.get('applies_when', '')}", f"Instruction: {prepared['instruction']}"]
        if prepared.get("example_reply"):
            parts.append(f"Example reply: {prepared['example_reply']}")
        prepared["text"] = "\n".join(parts)
    prepared["candidate_classification"] = classify_knowledge_candidate(prepared)
    if prepared["candidate_classification"] in {"duplicate", "uncertain"}:
        return None
    prepared["pending_action"] = {
        "material_addition": "review_material_addition",
        "conflict": "review_conflict",
        "replacement": "review_replacement",
        "genuinely_new": "review_new_knowledge",
    }[prepared["candidate_classification"]]
    return prepared


KNOWLEDGE_CLASSIFICATION_VERSION = 1
KNOWLEDGE_SCOPES = {"shared", "primary", "secondary", "internal"}
KNOWLEDGE_CATEGORIES = {
    "generic",
    "service_specific",
    "availability_or_booking_state",
    "customer_specific",
    "internal_or_uncertain",
}


def _quarantined_knowledge_classification() -> Dict[str, Any]:
    """Safe fallback: retain an entry but never expose an uncertain one."""
    return {
        "scope": "internal",
        "category": "internal_or_uncertain",
        "retrieval_enabled": False,
        "classification_version": KNOWLEDGE_CLASSIFICATION_VERSION,
        "classification_status": "quarantined",
    }


def classify_knowledge_entries(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Use the configured AI to permanently scope learned records in bounded batches."""
    fallback = {
        str(entry.get("id")): _quarantined_knowledge_classification()
        for entry in entries if str(entry.get("id", "")).strip()
    }
    openai_client = get_main_attr("openai_client", None)
    if not fallback or not openai_client:
        return fallback

    safe_records = [{
        "id": entry_id,
        "type": str(entry.get("type", "")),
        "text": str(entry.get("text", ""))[:2500],
        "topic": str(entry.get("topic", ""))[:500],
        "applies_when": str(entry.get("applies_when", ""))[:1000],
    } for entry in entries if (entry_id := str(entry.get("id", "")).strip())]
    instructions = (
        "Classify durable customer-service knowledge records. Return only JSON with a "
        "classifications array. Each item must contain id, scope, category and retrieval_enabled. "
        "scope is one of shared, primary, secondary, internal. category is one of generic, "
        "service_specific, availability_or_booking_state, customer_specific, internal_or_uncertain. "
        "Use shared only for durable facts safe for either SMS line. Use primary or secondary only "
        "when the record explicitly identifies that line. Mark availability, times, appointment "
        "options, booking states, one-off customer facts, personal information, and uncertainty as "
        "retrieval_enabled false. Durable service-specific information is valuable and may be "
        "retrieval_enabled true. Never treat stored service knowledge as availability; availability "
        "always comes from the live booking system. Never invent a scope or facts."
    )
    try:
        response = openai_client.responses.create(
            # Use the same configured responder model rather than assuming a
            # separate low-cost model is available in every deployment.
            model="gpt-5.6-terra",
            instructions=instructions,
            input=json.dumps({"records": safe_records}, ensure_ascii=False),
            store=False,
        )
        classifications = _parse_json_object(response.output_text or "").get("classifications", [])
    except Exception as exc:
        print(f"Knowledge classification failed; entries remain quarantined: {exc}")
        return fallback

    for item in classifications if isinstance(classifications, list) else []:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("id", "")).strip()
        scope = str(item.get("scope", "")).strip()
        category = str(item.get("category", "")).strip()
        if entry_id not in fallback or scope not in KNOWLEDGE_SCOPES or category not in KNOWLEDGE_CATEGORIES:
            continue
        retrieval_enabled = bool(item.get("retrieval_enabled", False))
        if category in {"availability_or_booking_state", "customer_specific", "internal_or_uncertain"}:
            retrieval_enabled = False
        fallback[entry_id] = {
            "scope": scope,
            "category": category,
            "retrieval_enabled": retrieval_enabled,
            "classification_version": KNOWLEDGE_CLASSIFICATION_VERSION,
            "classification_status": "classified",
        }
    return fallback


def classify_all_learned_information() -> Dict[str, int]:
    """Classify every unclassified learned JSONL entry and atomically save its tags."""
    knowledge_dir = get_main_attr("KNOWLEDGE_DIR", _DEFAULT_KNOWLEDGE_DIR)
    filepath = os.path.join(knowledge_dir, LEARNED_INFORMATION_FILENAME)
    if not os.path.exists(filepath):
        return {"classified": 0, "quarantined": 0, "total": 0}
    lock = get_main_attr("LEARNED_INFORMATION_LOCK", DEFAULT_LEARNED_INFORMATION_LOCK)
    with lock:
        records: List[Dict[str, Any]] = []
        retained_lines: List[str] = []
        with open(filepath, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    retained_lines.append(line)
                    continue
                if isinstance(item, dict) and str(item.get("id", "")).strip():
                    records.append(item)
                else:
                    retained_lines.append(line)

        pending = [
            record for record in records
            if record.get("classification_version") != KNOWLEDGE_CLASSIFICATION_VERSION
            or record.get("classification_status") != "classified"
        ]
        classifications: Dict[str, Dict[str, Any]] = {}
        for offset in range(0, len(pending), 10):
            classifications.update(classify_knowledge_entries(pending[offset:offset + 10]))
        for record in records:
            classification = classifications.get(str(record.get("id")))
            if classification:
                record.update(classification)
                record["classified_at"] = datetime.utcnow().isoformat() + "Z"
            retained_lines.append(json.dumps(record, ensure_ascii=False))
        temp_path = f"{filepath}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(retained_lines) + "\n")
        os.replace(temp_path, filepath)
    reload_fn = get_main_attr("load_knowledge_base")
    if callable(reload_fn):
        reload_fn()
    classified = sum(1 for item in classifications.values() if item.get("retrieval_enabled"))
    return {"classified": classified, "quarantined": len(classifications) - classified, "total": len(records)}


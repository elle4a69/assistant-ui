"""Learned information and manual learning curation service."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session

try:
    from backend.core.config import DATA_DIR, KNOWLEDGE_DIR, FIRST_CONTACT_ACCOUNT_KEYS
    from backend.core.state import LEARNED_INFORMATION_LOCK
    from backend.core.clients import openai_client
    from backend.models.domain import Thread, Message
    from backend.curator.classifier import (
        classify_knowledge_candidate,
        prepare_learning_candidate,
        classify_knowledge_entries,
        classify_all_learned_information,
        _knowledge_meaning_signature,
        KNOWLEDGE_CATEGORIES,
        KNOWLEDGE_SCOPES,
        _quarantined_knowledge_classification,
        KNOWLEDGE_CLASSIFICATION_VERSION,
    )
    from backend.curator.sanitizer import (
        has_unsafe_literal_learning_detail,
        shared_knowledge_is_generic,
        sanitise_reusable_knowledge_template,
        _learning_other_provider_detail,
    )
    from backend.curator.authority import (
        resolve_knowledge_authority,
        normalize_knowledge_record,
        _canonical_knowledge_key,
        _knowledge_reason,
        LEARNED_INFORMATION_FILENAME,
    )
    from backend.curator.supersession import _approve_curator_supersession_entry
    from backend.services.knowledge_service import load_knowledge_base
except ImportError:
    from core.config import DATA_DIR, KNOWLEDGE_DIR, FIRST_CONTACT_ACCOUNT_KEYS
    from core.state import LEARNED_INFORMATION_LOCK
    from core.clients import openai_client
    from models.domain import Thread, Message
    from curator.classifier import (
        classify_knowledge_candidate,
        prepare_learning_candidate,
        classify_knowledge_entries,
        classify_all_learned_information,
        _knowledge_meaning_signature,
        KNOWLEDGE_CATEGORIES,
        KNOWLEDGE_SCOPES,
        _quarantined_knowledge_classification,
        KNOWLEDGE_CLASSIFICATION_VERSION,
    )
    from curator.sanitizer import (
        has_unsafe_literal_learning_detail,
        shared_knowledge_is_generic,
        sanitise_reusable_knowledge_template,
        _learning_other_provider_detail,
    )
    from curator.authority import (
        resolve_knowledge_authority,
        normalize_knowledge_record,
        _canonical_knowledge_key,
        _knowledge_reason,
        LEARNED_INFORMATION_FILENAME,
    )
    from curator.supersession import _approve_curator_supersession_entry
    from services.knowledge_service import load_knowledge_base
logger = logging.getLogger(__name__)


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


LEARNED_INFORMATION_FILE = os.path.join(DATA_DIR, "learned_rules.json")

def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object.")
    return parsed


def save_learned_information(
    request_event_id: str,
    customer_question: str,
    supplied_information: str,
    knowledge_summary: str,
    account_key: str,
) -> str:
    """Atomically upsert one reusable learned-information record and refresh RAG."""
    entry = {
        "id": request_event_id,
        "type": "information_request_resolution",
        # Owner submissions are durable audit records first.  They are
        # classified before retrieval, but must not be discarded merely
        # because they cannot be converted into a reusable reply template.
        "source_type": "owner_information_submission",
        "canonical_key": _canonical_knowledge_key(knowledge_summary),
        "sms_account_key": account_key,
        "question": customer_question.strip(),
        "owner_information": supplied_information.strip(),
        "text": knowledge_summary.strip(),
        "created_at": datetime.utcnow().isoformat() + "Z",
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "status": "quarantined",
        "supersedes_id": None,
        "revision": 1,
        "review_status": "approved",
        "retrieval_enabled": False,
    }
    classify_fn = _dyn("classify_knowledge_entries", classify_knowledge_entries)
    classification = classify_fn([entry]).get(entry["id"], _quarantined_knowledge_classification())
    entry.update(classification)
    entry["scope"] = account_key if account_key in FIRST_CONTACT_ACCOUNT_KEYS else "internal"
    entry["source_account_key"] = account_key
    unsafe = (
        classification.get("category") in {
            "availability_or_booking_state", "customer_specific", "internal_or_uncertain",
        }
        or has_unsafe_literal_learning_detail(entry["text"])
    )
    entry["review_status"] = "pending" if unsafe else "approved"
    entry["status"] = "quarantined" if unsafe else "active"
    entry["retrieval_enabled"] = bool(
        classification.get("retrieval_enabled")
        and not unsafe
        and entry["scope"] in {"primary", "secondary"}
    )
    upsert_fn = _dyn("_upsert_learned_information_entry", _upsert_learned_information_entry)
    if upsert_fn(entry) is False:
        raise ValueError("The supplied information did not produce a safe, reusable knowledge record.")
    return LEARNED_INFORMATION_FILENAME


def _upsert_learned_information_entry(entry: Dict[str, Any]) -> bool:
    """Write one JSONL learning safely, retaining malformed legacy lines."""
    # All learning sources converge here.  Re-run the deterministic gate so a
    # UI client, staff-edited draft, or future caller cannot persist a duplicate
    # or unsafe reusable candidate by bypassing its earlier preview path.
    if entry.get("source_type") not in {"curator_proposal", "owner_information_submission"}:
        prepared = prepare_learning_candidate(entry)
        if prepared is None:
            return False
        entry = prepared
    entry_id = str(entry.get("id", "")).strip()
    if not entry_id:
        raise ValueError("A learned-information entry requires an id.")
    k_dir = _dyn("KNOWLEDGE_DIR", KNOWLEDGE_DIR)
    k_filename = _dyn("LEARNED_INFORMATION_FILENAME", LEARNED_INFORMATION_FILENAME)
    k_lock = _dyn("LEARNED_INFORMATION_LOCK", LEARNED_INFORMATION_LOCK)
    os.makedirs(k_dir, exist_ok=True)
    filepath = os.path.join(k_dir, k_filename)
    with k_lock:
        retained_lines = []
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.rstrip("\n")
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError:
                        retained_lines.append(line)
                        continue
                    if not isinstance(existing, dict) or existing.get("id") != entry_id:
                        retained_lines.append(line)
        retained_lines.append(json.dumps(entry, ensure_ascii=False))
        temp_path = f"{filepath}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(retained_lines) + "\n")
        os.replace(temp_path, filepath)
    load_knowledge_base()
    return True


def list_learned_information() -> List[Dict[str, Any]]:
    k_dir = _dyn("KNOWLEDGE_DIR", KNOWLEDGE_DIR)
    k_filename = _dyn("LEARNED_INFORMATION_FILENAME", LEARNED_INFORMATION_FILENAME)
    k_lock = _dyn("LEARNED_INFORMATION_LOCK", LEARNED_INFORMATION_LOCK)
    filepath = os.path.join(k_dir, k_filename)
    if not os.path.exists(filepath):
        return []
    records = []
    with k_lock, open(filepath, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and str(item.get("id", "")).strip():
                records.append(item)
    return sorted(records, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def replace_learned_information_entry(entry_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    k_dir = _dyn("KNOWLEDGE_DIR", KNOWLEDGE_DIR)
    k_filename = _dyn("LEARNED_INFORMATION_FILENAME", LEARNED_INFORMATION_FILENAME)
    k_lock = _dyn("LEARNED_INFORMATION_LOCK", LEARNED_INFORMATION_LOCK)
    filepath = os.path.join(k_dir, k_filename)
    if not os.path.exists(filepath):
        raise KeyError(entry_id)
    with k_lock:
        retained, updated_entry, found = [], None, 0
        for raw_line in open(filepath, "r", encoding="utf-8"):
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                retained.append(raw_line.rstrip("\n"))
                continue
            if not isinstance(item, dict) or item.get("id") != entry_id:
                retained.append(json.dumps(item, ensure_ascii=False))
                continue
            found += 1
            semantic_edit = any(key in updates for key in {
                "topic", "text", "scope", "applies_when", "instruction",
                "example_reply", "owner_topic", "owner_guidance",
            })
            item.update(updates)
            if semantic_edit:
                try:
                    item["revision"] = max(1, int(item.get("revision") or item.get("version") or 1)) + 1
                except (TypeError, ValueError):
                    item["revision"] = 2
                item["version"] = item["revision"]
                item["status"] = "quarantined"
                item["review_status"] = "pending"
                item["retrieval_enabled"] = False
            item["updated_at"] = datetime.utcnow().isoformat() + "Z"
            updated_entry = item
            retained.append(json.dumps(item, ensure_ascii=False))
        if found != 1 or updated_entry is None:
            raise KeyError(entry_id)
        temporary = f"{filepath}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write("\n".join(retained) + "\n")
        os.replace(temporary, filepath)
    load_knowledge_base()
    return updated_entry


def move_all_learned_information_to_review() -> int:
    """Quarantine existing learned entries until each is reviewed by staff."""
    count = 0
    for entry in list_learned_information():
        if entry.get("review_status") != "pending" or entry.get("retrieval_enabled") is not False:
            replace_learned_information_entry(entry["id"], {"review_status": "pending", "retrieval_enabled": False})
            count += 1
    return count


def approve_learned_information_entry(entry_id: str) -> Dict[str, Any]:
    entries = {entry["id"]: entry for entry in list_learned_information()}
    entry = entries.get(entry_id)
    if not entry:
        raise KeyError(entry_id)
    if entry.get("source_type") == "curator_proposal" and entry.get("proposed_supersedes_id"):
        return _approve_curator_supersession_entry(entry)
    classification = classify_knowledge_entries([entry]).get(entry_id, _quarantined_knowledge_classification())
    unsafe = (
        classification.get("category") in {"availability_or_booking_state", "customer_specific", "internal_or_uncertain"}
        or has_unsafe_literal_learning_detail(str(entry.get("text", "")))
    )
    updates = {
        "review_status": "approved",
        "status": "quarantined" if unsafe else "active",
        "category": classification.get("category", "internal_or_uncertain"),
        "classification_version": classification.get("classification_version", KNOWLEDGE_CLASSIFICATION_VERSION),
        "classification_status": classification.get("classification_status", "classified"),
        "retrieval_enabled": not unsafe and entry.get("scope") in {"shared", "primary", "secondary"},
    }
    if unsafe:
        updates["review_note"] = "Approved for audit only; literal time-sensitive, pricing, arrival, customer-specific, or uncertain material is not injected into AI replies."
    return replace_learned_information_entry(entry_id, updates)


def approve_pending_learned_information() -> Dict[str, int]:
    """Approve every item still in the review queue through the normal safety gate."""
    processed = 0
    active = 0
    restricted = 0
    for entry in list_learned_information():
        if entry.get("review_status") == "approved":
            continue
        approved = approve_learned_information_entry(entry["id"])
        processed += 1
        if approved.get("retrieval_enabled"):
            active += 1
        else:
            restricted += 1
    return {"processed": processed, "active": active, "restricted": restricted}


def approve_selected_learned_information(entry_ids: List[str]) -> Dict[str, int]:
    entries = {entry["id"]: entry for entry in list_learned_information()}
    missing = [entry_id for entry_id in entry_ids if entry_id not in entries]
    if missing:
        raise KeyError(missing[0])
    processed = 0
    active = 0
    restricted = 0
    for entry_id in entry_ids:
        if entries[entry_id].get("review_status") == "approved":
            continue
        approved = approve_learned_information_entry(entry_id)
        processed += 1
        if approved.get("retrieval_enabled"):
            active += 1
        else:
            restricted += 1
    return {"processed": processed, "active": active, "restricted": restricted}


def delete_learned_information_entry(entry_id: str) -> None:
    k_dir = _dyn("KNOWLEDGE_DIR", KNOWLEDGE_DIR)
    k_filename = _dyn("LEARNED_INFORMATION_FILENAME", LEARNED_INFORMATION_FILENAME)
    k_lock = _dyn("LEARNED_INFORMATION_LOCK", LEARNED_INFORMATION_LOCK)
    filepath = os.path.join(k_dir, k_filename)
    if not os.path.exists(filepath):
        raise KeyError(entry_id)
    with k_lock:
        retained, found = [], 0
        for raw_line in open(filepath, "r", encoding="utf-8"):
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                retained.append(raw_line.rstrip("\n"))
                continue
            if isinstance(item, dict) and item.get("id") == entry_id:
                found += 1
                continue
            retained.append(json.dumps(item, ensure_ascii=False))
        if found != 1:
            raise KeyError(entry_id)
        temporary = f"{filepath}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write("\n".join(retained) + "\n")
        os.replace(temporary, filepath)
    load_knowledge_base()


def generate_manual_learning(topic: str, owner_guidance: str) -> Dict[str, str]:
    """Structure rough owner notes without creating facts or a fallback entry."""
    client = _dyn("openai_client", openai_client)
    if not client:
        raise HTTPException(
            status_code=503,
            detail="The AI is unavailable, so nothing was added to learned material.",
        )

    instructions = (
        "You structure authoritative business-owner guidance for a customer-service AI knowledge base. "
        "Preserve the owner's meaning and distinguish an operational action from suggested wording. "
        "Do not invent facts, prices, availability, policies, names, locations, promises, or steps. "
        "Write instruction as a concise imperative describing what the AI should do. Only populate "
        "example_reply when the owner supplied wording or clearly asked what to say; otherwise use an "
        "empty string. Preserve useful reusable wording by replacing volatile details with only these approved "
        "tokens: {line_provider_name}, {line_information_url}, {website}, {suburb}, {service}, {price}, "
        "{date}, {time}. Never include a literal URL, booking link, phone number, price, payment term, deposit, "
        "availability claim, address, arrival direction, or customer-specific detail in a learned record. A link "
        "is dynamic line configuration, not learned knowledge. For availability, write an instruction to check "
        "the live calendar rather than claiming a templated time is free. "
        "Make applies_when specific enough for retrieval but broadly reusable. Return only "
        "valid JSON with exactly these string fields: topic, applies_when, instruction, example_reply."
    )
    prompt = f"Owner topic or situation:\n{topic}\n\nOwner's rough guidance:\n{owner_guidance}"
    try:
        response = client.responses.create(
            model="gpt-5.6-terra",
            instructions=instructions,
            input=prompt,
            store=False,
        )
        result = _parse_json_object(response.output_text or "")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="The AI could not structure this learning. Nothing was saved.",
        ) from exc

    expected_fields = {"topic", "applies_when", "instruction", "example_reply"}
    if set(result) != expected_fields:
        raise HTTPException(
            status_code=502,
            detail="The AI returned the wrong learning format. Nothing was saved.",
        )
    normalized = {
        key: str(result.get(key, "")).strip()
        for key in ("topic", "applies_when", "instruction", "example_reply")
    }
    if not normalized["topic"] or not normalized["applies_when"] or not normalized["instruction"]:
        raise HTTPException(
            status_code=502,
            detail="The AI returned an incomplete learning. Nothing was saved.",
        )
    if any(len(value) > 3000 for value in normalized.values()):
        raise HTTPException(
            status_code=502,
            detail="The structured learning was unexpectedly long. Nothing was saved.",
        )
    if any(has_unsafe_literal_learning_detail(value) for value in normalized.values()):
        raise HTTPException(
            status_code=502,
            detail="The AI included a literal URL or other volatile detail in learned material. Nothing was saved.",
        )
    return normalized


def save_manual_learning(
    topic: str,
    owner_guidance: str,
    structured: Dict[str, str],
    scope: str = "shared",
) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat() + "Z"
    text_parts = [
        f"Topic: {structured['topic']}",
        f"Applies when: {structured['applies_when']}",
        f"Instruction: {structured['instruction']}",
    ]
    if structured.get("example_reply"):
        text_parts.append(f"Example reply: {structured['example_reply']}")
    entry = {
        "id": f"manual-{uuid.uuid4()}",
        "type": "manual_guidance",
        "source_type": "manual_guidance",
        "canonical_key": _canonical_knowledge_key(structured["topic"]),
        "sms_account_key": scope if scope in {"primary", "secondary"} else "shared",
        "topic": structured["topic"],
        "applies_when": structured["applies_when"],
        "instruction": structured["instruction"],
        "example_reply": structured.get("example_reply", ""),
        "owner_topic": topic.strip(),
        "owner_guidance": owner_guidance.strip(),
        "text": "\n".join(text_parts),
        "created_at": now,
        "updated_at": now,
        "status": "quarantined",
        "supersedes_id": None,
        "revision": 1,
        "review_status": "pending",
        "retrieval_enabled": False,
        "review_source": "ai-drafted",
    }
    entry.update(classify_knowledge_entries([entry]).get(entry["id"], _quarantined_knowledge_classification()))
    entry["review_status"] = "pending"
    entry["retrieval_enabled"] = False
    if scope in {"primary", "secondary"}:
        entry["scope"] = scope
        entry["source_account_key"] = scope
    _upsert_learned_information_entry(entry)
    return entry


def redraft_learned_information_entry(entry_id: str) -> Dict[str, Any]:
    """Ask the learning-only curator to improve one pending record for review."""
    fn_list = _dyn("list_learned_information", list_learned_information)
    entries = {entry["id"]: entry for entry in fn_list()}
    entry = entries.get(entry_id)
    if not entry:
        raise KeyError(entry_id)
    source_topic = str(entry.get("owner_topic") or entry.get("topic") or "Learned guidance")
    source_guidance = str(entry.get("owner_guidance") or entry.get("text") or "").strip()
    if not source_guidance:
        raise ValueError("This learned entry has no text to redraft.")
    fn_generate = _dyn("generate_manual_learning", generate_manual_learning)
    structured = fn_generate(source_topic, source_guidance)
    text_parts = [
        f"Topic: {structured['topic']}",
        f"Applies when: {structured['applies_when']}",
        f"Instruction: {structured['instruction']}",
    ]
    if structured.get("example_reply"):
        text_parts.append(f"Example reply: {structured['example_reply']}")
    fn_replace = _dyn("replace_learned_information_entry", replace_learned_information_entry)
    return fn_replace(entry_id, {
        "topic": structured["topic"],
        "applies_when": structured["applies_when"],
        "instruction": structured["instruction"],
        "example_reply": structured.get("example_reply", ""),
        "text": "\n".join(text_parts),
        "review_status": "pending",
        "retrieval_enabled": False,
        "review_source": "ai-redrafted",
    })


def redraft_all_pending_learned_information() -> Dict[str, int]:
    """Run the learning-only curator over pending entries without approving any."""
    processed = 0
    failed = 0
    fn_list = _dyn("list_learned_information", list_learned_information)
    fn_redraft = _dyn("redraft_learned_information_entry", redraft_learned_information_entry)
    for entry in fn_list():
        if entry.get("review_status") != "pending":
            continue
        try:
            fn_redraft(entry["id"])
            processed += 1
        except Exception as exc:
            failed += 1
            print(f"Learning redraft failed for {entry.get('id')}: {type(exc).__name__}")
    return {"processed": processed, "failed": failed}


def save_edited_draft_learning(db: Session, thread: Thread, draft: Message) -> Optional[Dict[str, Any]]:
    """Turn a staff-edited draft into safely classified reusable guidance.

    The classifier is the gatekeeper: appointment details, current availability,
    names and other customer-specific material are retained only as quarantined
    audit knowledge and are never supplied to a later customer conversation.
    """
    customer_message = db.query(Message).filter(
        Message.thread_id == thread.id,
        Message.role == "customer",
        Message.at <= draft.at,
    ).order_by(Message.at.desc(), Message.id.desc()).first()
    if not customer_message:
        return None
    now = datetime.utcnow().isoformat() + "Z"
    entry = {
        "id": f"edited-draft-{draft.id}",
        "type": "staff_edited_draft",
        "source_type": "staff_edited_draft",
        "canonical_key": _canonical_knowledge_key("Staff-approved customer response"),
        "sms_account_key": thread.sms_account_key if thread.sms_account_key in FIRST_CONTACT_ACCOUNT_KEYS else "internal",
        "topic": "Staff-approved customer response",
        "applies_when": customer_message.text.strip()[:1000],
        "instruction": "Use the approved response only when its facts are durable and relevant.",
        "example_reply": draft.text.strip(),
        "question": customer_message.text.strip(),
        "owner_information": draft.text.strip(),
        "text": (
            f"Customer question: {customer_message.text.strip()}\n"
            f"Staff-approved reply: {draft.text.strip()}"
        ),
        "created_at": now,
        "updated_at": now,
        "status": "quarantined",
        "supersedes_id": None,
        "revision": 1,
        "review_status": "pending",
        "retrieval_enabled": False,
        "review_source": "staff-edited-reply",
    }
    entry.update(
        classify_knowledge_entries([entry]).get(
            entry["id"], _quarantined_knowledge_classification()
        )
    )
    entry["scope"] = thread.sms_account_key if thread.sms_account_key in FIRST_CONTACT_ACCOUNT_KEYS else "internal"
    entry["source_account_key"] = thread.sms_account_key
    entry["review_status"] = "pending"
    entry["retrieval_enabled"] = False
    _upsert_learned_information_entry(entry)
    return entry


__all__ = [
    "LEARNED_INFORMATION_FILE",
    "_parse_json_object",
    "save_learned_information",
    "_upsert_learned_information_entry",
    "list_learned_information",
    "replace_learned_information_entry",
    "move_all_learned_information_to_review",
    "approve_learned_information_entry",
    "approve_pending_learned_information",
    "approve_selected_learned_information",
    "delete_learned_information_entry",
    "generate_manual_learning",
    "save_manual_learning",
    "redraft_learned_information_entry",
    "redraft_all_pending_learned_information",
    "save_edited_draft_learning",
]

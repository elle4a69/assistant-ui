"""Knowledge Curator service: audits, integrity inspections, and proposal lifecycles."""

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException

from .authority import (
    KNOWLEDGE_RECORD_STATUSES,
    LEARNED_INFORMATION_FILENAME,
    LEARNING_REVIEW_STATUSES,
    _knowledge_bool,
    _knowledge_timestamp,
    _knowledge_timestamp_text,
    normalize_knowledge_record,
    resolve_knowledge_authority,
)
from .compat import get_main_attr
from .sanitizer import (
    _curator_dynamic_claim_detail,
    _curator_dynamic_claim_kind,
    _learning_other_provider_detail,
    has_unsafe_literal_learning_detail,
)

_DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

KNOWLEDGE_CURATOR_STATE_PATH = os.path.join(_DEFAULT_DATA_DIR, "knowledge_curator_state.json")
KNOWLEDGE_CURATOR_LOCK = threading.Lock()
KNOWLEDGE_CURATOR_MAX_RUNS = 50
KNOWLEDGE_CURATOR_MAX_PROPOSALS = 500
KNOWLEDGE_CURATOR_MAX_MAINTENANCE_AUDITS = 50
KNOWLEDGE_CURATOR_MAX_BACKUPS = 10
# Use the deployment's supported model selection when it is configured.  The
# fallback preserves the application default for existing installations.
KNOWLEDGE_CURATOR_MODEL = os.getenv("KNOWLEDGE_CURATOR_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5.6-terra"
KNOWLEDGE_CURATOR_FINDING_TYPES = {
    "exact_duplicate",
    "incompatible_active_records",
    "expired_record",
    "future_record",
    "dangling_supersession",
    "cyclic_supersession",
    "cross_topic_supersession",
    "cross_scope_supersession",
    "branched_supersession",
    "invalid_metadata",
    "literal_dynamic_authority",
    "shared_provider_specific",
    "apparently_superseded",
    "owner_answer_required",
}
KNOWLEDGE_CURATOR_ACTIONS = {
    "no_action",
    "ask_owner",
    "draft_replacement",
    "draft_supersession",
    "quarantine_for_review",
    "merge_duplicate",
}
KNOWLEDGE_CURATOR_UNRESOLVED_STATUSES = {"proposed", "accepted"}
KNOWLEDGE_CURATOR_RESOLUTIONS = {
    "keep_all_examples", "select_current_rule", "create_merged_draft", "needs_manual_investigation",
    "keep_both_distinct", "create_consolidation_draft", "create_metadata_repair_draft",
    "add_safe_replacement_draft", "not_an_issue", "dismiss_for_now",
}
KNOWLEDGE_CURATOR_CONTEXTUAL_SOURCE_MARKERS = ("sms", "pair", "staff-edited", "staff_edited")



def _get_curator_state_path() -> str:
    path = get_main_attr("KNOWLEDGE_CURATOR_STATE_PATH")
    if path:
        return path
    data_dir = get_main_attr("DATA_DIR", _DEFAULT_DATA_DIR)
    return os.path.join(data_dir, "knowledge_curator_state.json")


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object.")
    return parsed

def _openai_error_code(exc: Exception) -> str:
    """Return a structured provider error code without inspecting secret text."""
    candidates: List[Any] = [getattr(exc, "code", None)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        candidates.extend((body.get("code"), (body.get("error") or {}).get("code") if isinstance(body.get("error"), dict) else None))
    error = getattr(exc, "error", None)
    if isinstance(error, dict):
        candidates.append(error.get("code"))
    response = getattr(exc, "response", None)
    response_data = getattr(response, "data", None)
    if isinstance(response_data, dict):
        candidates.extend((response_data.get("code"), (response_data.get("error") or {}).get("code") if isinstance(response_data.get("error"), dict) else None))
    normalized = [str(value or "").strip().casefold()[:120] for value in candidates if str(value or "").strip()]
    quota_codes = {"insufficient_quota", "billing_hard_limit_reached", "billing_hard_limit"}
    return next((code for code in normalized if code in quota_codes), normalized[0] if normalized else "")


def is_openai_quota_exhausted(exc: Exception) -> bool:
    """Classify only genuine structured billing/quota exhaustion responses."""
    return _openai_error_code(exc) in {
        "insufficient_quota",
        "billing_hard_limit_reached",
        "billing_hard_limit",
    }


def _curator_empty_state() -> Dict[str, Any]:
    return {"version": 2, "runs": [], "proposals": [], "maintenance_history": []}


def _bound_curator_proposals(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unresolved = [item for item in proposals if item.get("status") in KNOWLEDGE_CURATOR_UNRESOLVED_STATUSES]
    resolved = [item for item in proposals if item.get("status") not in KNOWLEDGE_CURATOR_UNRESOLVED_STATUSES]
    if len(unresolved) >= KNOWLEDGE_CURATOR_MAX_PROPOSALS:
        return unresolved[-KNOWLEDGE_CURATOR_MAX_PROPOSALS:]
    return resolved[-(KNOWLEDGE_CURATOR_MAX_PROPOSALS - len(unresolved)):] + unresolved


def _load_curator_state() -> Dict[str, Any]:
    try:
        with open(_get_curator_state_path(), "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return _curator_empty_state()
    if not isinstance(state, dict):
        return _curator_empty_state()
    # Version 1 had the same safe proposal identity but no response-only
    # previews.  Read it losslessly enough for audit history, while dropping
    # anything outside the bounded, content-minimised state contract.
    runs = state.get("runs") if isinstance(state.get("runs"), list) else []
    proposals = state.get("proposals") if isinstance(state.get("proposals"), list) else []
    normalized = [_curator_safe_state_proposal(item) for item in proposals if isinstance(item, dict)]
    normalized_runs = [_curator_safe_state_run(item) for item in runs if isinstance(item, dict)]
    maintenance = state.get("maintenance_history") if isinstance(state.get("maintenance_history"), list) else []
    return {
        "version": 2,
        "runs": normalized_runs[-KNOWLEDGE_CURATOR_MAX_RUNS:],
        "proposals": _bound_curator_proposals(normalized),
        "maintenance_history": [_curator_safe_maintenance_entry(item) for item in maintenance if isinstance(item, dict)][-KNOWLEDGE_CURATOR_MAX_MAINTENANCE_AUDITS:],
    }


def _curator_safe_state_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """Keep migration history bounded and free of record/source payloads."""
    counts = run.get("finding_counts") if isinstance(run.get("finding_counts"), dict) else {}
    def bounded_count(value: Any) -> int:
        try:
            return min(100000, max(0, int(value)))
        except (TypeError, ValueError):
            return 0
    return {
        "id": str(run.get("id") or "")[:160],
        "status": str(run.get("status") or "")[:80],
        "error_code": str(run.get("error_code") or "")[:120] or None,
        "message": str(run.get("message") or "")[:500],
        "started_at": str(run.get("started_at") or "")[:64],
        "completed_at": str(run.get("completed_at") or "")[:64],
        "finding_counts": {str(key)[:80]: bounded_count(value) for key, value in counts.items() if isinstance(value, int) and not isinstance(value, bool)},
        "finding_count": bounded_count(run.get("finding_count")),
        "created_proposals": bounded_count(run.get("created_proposals")),
        "safe_repairs_completed": bounded_count(run.get("safe_repairs_completed")),
        "ai_helper_status": str(run.get("ai_helper_status") or "")[:80] or None,
    }


def _curator_safe_maintenance_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Persist only bounded repair bookkeeping, never source content."""
    repairs = entry.get("repairs") if isinstance(entry.get("repairs"), list) else []
    safe_repairs = []
    for repair in repairs[:1000]:
        if not isinstance(repair, dict):
            continue
        record_id = str(repair.get("record_id") or "").strip()[:160]
        fields = [str(field)[:80] for field in repair.get("fields", []) if isinstance(field, str)][:12]
        if record_id and fields:
            safe_repairs.append({"record_id": record_id, "fields": fields})
    try:
        repaired_count = min(100000, max(0, int(entry.get("repaired_count") or 0)))
    except (TypeError, ValueError):
        repaired_count = 0
    return {
        "id": str(entry.get("id") or "")[:160],
        "timestamp": str(entry.get("timestamp") or "")[:64],
        "result": str(entry.get("result") or "")[:80],
        "repaired_count": repaired_count,
        "repairs": safe_repairs,
    }


def _curator_safe_state_proposal(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Drop accidental response-only source content before durable storage."""
    allowed = {
        "id", "fingerprint", "canonical_key", "scope", "finding_type", "records", "reason_codes", "evidence",
        "proposed_action", "replacement_draft", "confidence", "owner_questions", "status", "created_at", "updated_at",
        "last_seen_at", "draft_entry_id", "resolution", "selected_record_ids", "malformed_references", "malformed_reference_count",
    }
    safe = {key: value for key, value in proposal.items() if key in allowed}
    # Inspect the original container before normalising it.  Dropping a bad
    # ref would turn a mixed proposal into an actionable subset.
    safe_refs, malformed = _curator_sanitize_record_references(proposal.get("records"))
    # A prior safe migration no longer retains the malformed payload itself;
    # retain its generated evidence too, but never accept a caller-supplied
    # count as proof that no malformed reference existed.
    persisted_malformed = _curator_safe_malformed_references(proposal.get("malformed_references"))
    if isinstance(proposal.get("records"), list) and not proposal.get("records") and persisted_malformed:
        malformed = []
    malformed.extend(persisted_malformed)
    unique_malformed = {(item.get("position"), item.get("reason_code")): item for item in malformed}
    safe["records"] = safe_refs[:20]
    safe["malformed_references"] = list(unique_malformed.values())[:20]
    safe["malformed_reference_count"] = len(safe["malformed_references"])
    details = safe.get("evidence") if isinstance(safe.get("evidence"), dict) else {}
    safe["evidence"] = {
        "reason_codes": [str(code)[:120] for code in details.get("reason_codes", [])][:10],
        "record_count": min(20, max(0, int(details.get("record_count") or 0))),
        **{key: details[key] for key in ("missing_fields", "invalid_fields", "dynamic_type", "same_applicability", "applicability_status", "source_role") if key in details},
    }
    safe["selected_record_ids"] = [str(value)[:160] for value in safe.get("selected_record_ids", [])][:20]
    if safe.get("resolution") not in KNOWLEDGE_CURATOR_RESOLUTIONS:
        safe.pop("resolution", None)
    return safe


KNOWLEDGE_CURATOR_MALFORMED_REFERENCE_REASONS = {
    "records_not_list", "empty_records", "reference_not_object", "missing_reference_id", "invalid_reference_revision",
}


def _curator_sanitize_record_references(value: Any) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Produce usable refs and content-free evidence for every bad ref."""
    if not isinstance(value, list):
        return [], [{"position": None, "reference_status": "malformed", "reason_code": "records_not_list"}]
    if not value:
        return [], [{"position": None, "reference_status": "malformed", "reason_code": "empty_records"}]
    valid, malformed = [], []
    for position, ref in enumerate(value):
        reason = ""
        if not isinstance(ref, dict):
            reason = "reference_not_object"
        elif not str(ref.get("id") or "").strip():
            reason = "missing_reference_id"
        elif isinstance(ref.get("revision"), bool) or not isinstance(ref.get("revision"), int) or int(ref["revision"]) < 1:
            reason = "invalid_reference_revision"
        if reason:
            malformed.append({"position": position, "reference_status": "malformed", "reason_code": reason})
        else:
            valid.append({"id": str(ref["id"])[:160], "revision": int(ref["revision"])})
    return valid, malformed


def _curator_safe_malformed_references(value: Any) -> List[Dict[str, Any]]:
    """Read only prior generated metadata, never arbitrary malformed input."""
    if not isinstance(value, list):
        return []
    safe = []
    for item in value:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason_code") or "")
        position = item.get("position")
        if reason not in KNOWLEDGE_CURATOR_MALFORMED_REFERENCE_REASONS:
            continue
        if position is not None and (isinstance(position, bool) or not isinstance(position, int) or position < 0):
            continue
        safe.append({"position": position, "reference_status": "malformed", "reason_code": reason})
    return safe


def _save_curator_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_get_curator_state_path()), exist_ok=True)
    bounded = {
        "version": 2,
        "runs": [_curator_safe_state_run(item) for item in state.get("runs", []) if isinstance(item, dict)][-KNOWLEDGE_CURATOR_MAX_RUNS:],
        "proposals": _bound_curator_proposals([_curator_safe_state_proposal(item) for item in state.get("proposals", []) if isinstance(item, dict)]),
        "maintenance_history": [_curator_safe_maintenance_entry(item) for item in state.get("maintenance_history", []) if isinstance(item, dict)][-KNOWLEDGE_CURATOR_MAX_MAINTENANCE_AUDITS:],
    }
    temporary = f"{_get_curator_state_path()}.{uuid.uuid4().hex}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(bounded, handle, ensure_ascii=False, sort_keys=True)
    os.replace(temporary, _get_curator_state_path())


def _curator_record_preview(record: Dict[str, Any]) -> Dict[str, Any]:
    """Authenticated Settings-only projection; deliberately never persisted."""
    raw = record.get("_raw") if isinstance(record.get("_raw"), dict) else record
    return {
        "id": str(record.get("id") or ""), "revision": int(record.get("revision") or 1),
        "scope": str(record.get("scope") or "internal"), "sms_line": str(record.get("sms_account_key") or "internal"),
        "source_type": str(record.get("source_type") or ""), "topic": str(raw.get("topic") or ""),
        "canonical_key": str(record.get("canonical_key") or ""),
        "customer_message": str(raw.get("customer_message") or raw.get("customer") or raw.get("question") or ""),
        "applies_when": str(raw.get("applies_when") or ""),
        "approved_reply": str(raw.get("approved_reply") or raw.get("reply") or raw.get("instruction") or raw.get("example_reply") or ""),
        "instruction": str(raw.get("instruction") or ""), "example_reply": str(raw.get("example_reply") or ""),
        "knowledge_text": str(record.get("text") or ""), "status": str(record.get("status") or ""),
        "review_status": str(record.get("review_status") or ""), "retrieval_enabled": _knowledge_bool(record.get("retrieval_enabled")),
        "created_at": str(record.get("created_at") or ""), "updated_at": str(record.get("updated_at") or ""),
        "metadata_issues": _curator_invalid_metadata_fields(record),
    }


def _curator_reference_statuses(proposal: Dict[str, Any], current: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve refs exactly; never substitute a newer record for an old one."""
    refs = proposal.get("records")
    valid_refs, malformed = _curator_sanitize_record_references(refs)
    persisted_malformed = _curator_safe_malformed_references(proposal.get("malformed_references"))
    if isinstance(refs, list) and not refs and persisted_malformed:
        malformed = []
    malformed.extend(persisted_malformed)
    statuses: List[Dict[str, Any]] = []
    for ref in valid_refs:
        record_id, expected = str(ref["id"])[:160], int(ref["revision"])
        record = current.get(record_id)
        if not record:
            statuses.append({"reference_status": "missing", "id": record_id, "expected_revision": expected, "current_revision": None})
            continue
        actual = int(record.get("revision") or record.get("version") or 1)
        if actual != expected:
            statuses.append({"reference_status": "stale", "id": record_id, "expected_revision": expected, "current_revision": actual})
            continue
        statuses.append({"reference_status": "current", "id": record_id, "expected_revision": expected, "current_revision": actual, "_record": record})
    for item in {(entry.get("position"), entry.get("reason_code")): entry for entry in malformed}.values():
        statuses.append({"reference_status": "malformed", "id": "", "expected_revision": None, "current_revision": None, "position": item.get("position"), "reason_code": item.get("reason_code")})
    return statuses


def _curator_reference_status_preview(item: Dict[str, Any]) -> Dict[str, Any]:
    preview = {key: item.get(key) for key in ("reference_status", "id", "expected_revision", "current_revision")}
    if item.get("reference_status") == "malformed":
        preview["position"] = item.get("position")
        preview["reason_code"] = item.get("reason_code")
    preview["revision"] = item.get("expected_revision") or 0
    return preview


def _present_curator_proposal(proposal: Dict[str, Any], current: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Attach response-only previews and excerpts to a durable safe proposal."""
    result = dict(proposal)
    reference_statuses = _curator_reference_statuses(proposal, current)
    referenced = [item.get("_record") for item in reference_statuses if item.get("reference_status") == "current"]
    result["record_previews"] = [
        {"reference_status": "current", **_curator_record_preview(item["_record"])} if item.get("reference_status") == "current"
        else _curator_reference_status_preview(item)
        for item in reference_statuses
    ]
    result["actionable"] = all(item.get("reference_status") == "current" for item in reference_statuses)
    evidence = dict(result.get("evidence") or {})
    for item in referenced:
        if item and evidence.get("dynamic_type"):
            detail = _curator_dynamic_claim_detail(str(item.get("text") or ""))
            if detail and detail["kind"] == evidence["dynamic_type"]:
                evidence["trigger_excerpt"] = detail["excerpt"]
                break
    result["evidence"] = evidence
    return result


def _present_curator_state(state: Dict[str, Any], *, unresolved_only: bool = False) -> Dict[str, Any]:
    current = {str(item.get("id")): item for item in _curator_records()}
    proposals = state.get("proposals", [])
    if unresolved_only:
        proposals = [item for item in proposals if item.get("status") in KNOWLEDGE_CURATOR_UNRESOLVED_STATUSES]
    return {"runs": list(reversed(state.get("runs", []))), "proposals": [_present_curator_proposal(item, current) for item in reversed(proposals)]}


def _curator_records() -> List[Dict[str, Any]]:
    """Return one normalized copy of every durable knowledge record."""
    records: Dict[str, Dict[str, Any]] = {}
    list_learned = get_main_attr("list_learned_information")
    learned_items = list_learned() if callable(list_learned) else []
    for index, raw in enumerate(learned_items):
        normalized = normalize_knowledge_record(raw, source=LEARNED_INFORMATION_FILENAME, source_index=index, learned=True)
        normalized["_raw"] = raw
        normalized["_canonical_key_explicit"] = bool(raw.get("canonical_key") or raw.get("key") or raw.get("topic"))
        records[normalized["id"]] = normalized
    knowledge_chunks = get_main_attr("KNOWLEDGE_CHUNKS", [])
    for index, raw in enumerate(knowledge_chunks):
        normalized = normalize_knowledge_record(raw, source=str(raw.get("source") or "memory"), source_index=index,
                                                learned=str(raw.get("source") or "") == LEARNED_INFORMATION_FILENAME)
        normalized["_canonical_key_explicit"] = bool(raw.get("canonical_key") or raw.get("key") or raw.get("topic"))
        records.setdefault(normalized["id"], normalized)
    return sorted(records.values(), key=lambda item: item["id"])


def _curator_authority_role(item: Dict[str, Any]) -> str:
    """Semantic role for deterministic collisions, never a raw source label."""
    source = str(item.get("source_type") or item.get("type") or "").casefold()
    return "contextual_example" if any(marker in source for marker in KNOWLEDGE_CURATOR_CONTEXTUAL_SOURCE_MARKERS) else "authoritative_rule"


# Kept as a private compatibility alias for any integrations using Phase 2.
_curator_source_role = _curator_authority_role


def _curator_applicability_key(item: Dict[str, Any]) -> str:
    value = str(item.get("applies_when") or "").strip().casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _curator_invalid_metadata_fields(item: Dict[str, Any]) -> Dict[str, List[str]]:
    raw = item.get("_raw") if isinstance(item.get("_raw"), dict) else item
    missing, invalid = [], []
    required = ("id", "text", "created_at")
    for field in required:
        if not str(raw.get(field) or "").strip():
            missing.append(field)
    if not str(raw.get("updated_at") or raw.get("created_at") or "").strip():
        missing.append("updated_at")
    if not str(item.get("canonical_key") or "").strip() or not item.get("_canonical_key_explicit", True):
        missing.append("canonical_key")
    if str(raw.get("scope") or raw.get("sms_account_key") or "") not in {"shared", "primary", "secondary", "internal"}:
        invalid.append("scope")
    if str(raw.get("status") or "") not in KNOWLEDGE_RECORD_STATUSES:
        invalid.append("status")
    if str(raw.get("review_status") or "") not in LEARNING_REVIEW_STATUSES:
        invalid.append("review_status")
    if not _curator_valid_revision(raw.get("revision") or raw.get("version")):
        invalid.append("revision")
    if raw.get("created_at") and not _knowledge_timestamp(raw.get("created_at")):
        invalid.append("created_at")
    if raw.get("updated_at") and not _knowledge_timestamp(raw.get("updated_at")):
        invalid.append("updated_at")
    if raw.get("effective_from") and not _knowledge_timestamp(raw.get("effective_from")):
        invalid.append("effective_from")
    if raw.get("effective_until") and not _knowledge_timestamp(raw.get("effective_until")):
        invalid.append("effective_until")
    if _knowledge_timestamp(raw.get("effective_from")) and _knowledge_timestamp(raw.get("effective_until")) and _knowledge_timestamp(raw.get("effective_from")) > _knowledge_timestamp(raw.get("effective_until")):
        invalid.append("effective_period")
    return {"missing_fields": sorted(set(missing)), "invalid_fields": sorted(set(invalid))}


def _curator_valid_revision(value: Any) -> bool:
    try:
        return int(value) >= 1
    except (TypeError, ValueError):
        return False


def _curator_finding(
    finding_type: str,
    records: List[Dict[str, Any]],
    *,
    reason_code: str,
    action: str,
    owner_question: str = "",
    replacement_draft: Optional[Dict[str, str]] = None,
    evidence_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    record_refs = sorted(
        ({"id": str(item.get("id", ""))[:160], "revision": int(item.get("revision") or 1)} for item in records),
        key=lambda item: (item["id"], item["revision"]),
    )
    scope = str(records[0].get("sms_account_key") or "internal") if records else "internal"
    canonical_key = (
        str(records[0].get("canonical_key") or "")[:160]
        if records and records[0].get("_canonical_key_explicit", True)
        else "uncategorised"
    )
    fingerprint_material = json.dumps({
        "finding_type": finding_type,
        "canonical_key": canonical_key,
        "scope": scope,
        "records": record_refs,
        "reason_code": reason_code,
    }, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()
    return {
        "fingerprint": fingerprint,
        "canonical_key": canonical_key,
        "scope": scope,
        "finding_type": finding_type,
        "records": record_refs,
        "reason_codes": [reason_code],
        # This remains deliberately content-free.  Authenticated response
        # presentation resolves previews and trigger excerpts on demand.
        "evidence": {"reason_codes": [reason_code], "record_count": len(record_refs), **(evidence_details or {})},
        "proposed_action": action if action in KNOWLEDGE_CURATOR_ACTIONS else "ask_owner",
        "replacement_draft": replacement_draft,
        "confidence": "deterministic",
        "owner_questions": [owner_question[:500]] if owner_question else [],
    }


def inspect_knowledge_integrity(records: Optional[List[Dict[str, Any]]] = None, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Run content-minimising, deterministic checks before any model call."""
    current = _knowledge_timestamp(now or datetime.utcnow()) or datetime.utcnow()
    if records is None:
        items = _curator_records()
    else:
        items = []
        for index, raw in enumerate(records):
            normalized = normalize_knowledge_record(raw, source=str(raw.get("source") or "memory"), source_index=index,
                                                    learned=str(raw.get("source") or "") == LEARNED_INFORMATION_FILENAME)
            normalized["_raw"] = raw
            normalized["_canonical_key_explicit"] = bool(raw.get("canonical_key") or raw.get("key") or raw.get("topic"))
            items.append(normalized)
    findings: List[Dict[str, Any]] = []
    by_id = {str(item.get("id")): item for item in items if str(item.get("id", "")).strip()}
    children: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        predecessor = str(item.get("supersedes_id") or "")
        if predecessor:
            children[predecessor].append(item)

        metadata_issues = _curator_invalid_metadata_fields(item)
        if metadata_issues["missing_fields"] or metadata_issues["invalid_fields"]:
            findings.append(_curator_finding(
                "invalid_metadata", [item], reason_code="curator_invalid_metadata", action="quarantine_for_review",
                evidence_details=metadata_issues,
            ))

        effective_from = _knowledge_timestamp(item.get("effective_from"))
        effective_until = _knowledge_timestamp(item.get("effective_until"))
        if effective_from and current < effective_from:
            findings.append(_curator_finding("future_record", [item], reason_code="curator_future_record", action="no_action"))
        if item.get("status") == "expired" or effective_until and current > effective_until:
            findings.append(_curator_finding("expired_record", [item], reason_code="curator_expired_record", action="quarantine_for_review"))

        if item.get("review_status") == "approved":
            dynamic_detail = _curator_dynamic_claim_detail(str(item.get("text") or ""))
            if dynamic_detail:
                dynamic_kind = dynamic_detail["kind"]
                instruction = {
                    "price": "Use the current configured service price and duration from Settings.",
                    "duration": "Use the current configured service price and duration from Settings.",
                    "availability": "Check the live calendar before discussing available dates or times.",
                    "booking_time": "Check the live calendar before discussing booking dates or times.",
                }[dynamic_kind]
                draft = {"topic": str(item.get("canonical_key") or "Dynamic authority"), "instruction": instruction, "text": instruction}
                findings.append(_curator_finding(
                    "literal_dynamic_authority", [item], reason_code=f"curator_literal_{dynamic_kind}", action="draft_supersession",
                    replacement_draft=draft, evidence_details={"dynamic_type": dynamic_kind},
                ))

        if item.get("scope") == "shared":
            text = str(item.get("text") or "")
            account_keys = get_main_attr("FIRST_CONTACT_ACCOUNT_KEYS", ("primary", "secondary"))
            matches = [key for key in account_keys if _learning_other_provider_detail(text, "secondary" if key == "primary" else "primary")]
            if matches:
                findings.append(_curator_finding(
                    "shared_provider_specific", [item], reason_code="curator_shared_provider_specific",
                    action="ask_owner",
                    owner_question="This shared rule contains provider-specific details. Select its proven provider before any scope change; no provider has been guessed.",
                ))

        if item.get("scope") == "internal" or item.get("category") == "internal_or_uncertain":
            findings.append(_curator_finding(
                "owner_answer_required", [item], reason_code="curator_owner_answer_required", action="ask_owner",
                owner_question="This information is currently kept out of customer replies. Should the agent ever use it?",
            ))

    # Exact duplicates and incompatible independent active authorities.  A
    # shared broad topic is not a collision identity: message pairs and staff
    # edits are contextual examples, and authority requires a specific shared
    # applicability condition as well as a matching role/source type.
    by_exact: Dict[tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    by_key: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    by_collision: Dict[tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = (str(item.get("scope")), str(item.get("canonical_key")))
        by_key[key].append(item)
        normalized_text = re.sub(r"\s+", " ", str(item.get("text") or "").strip().casefold())
        by_exact[(key[0], key[1], normalized_text)].append(item)
        applicability = _curator_applicability_key(item)
        authority_role = _curator_authority_role(item)
        if authority_role == "authoritative_rule" and applicability:
            by_collision[(key[0], key[1], authority_role, applicability)].append(item)
    for group in by_exact.values():
        if len(group) > 1:
            findings.append(_curator_finding("exact_duplicate", group, reason_code="curator_exact_duplicate", action="merge_duplicate"))
    for group in by_key.values():
        active_authorities = [item for item in group if item.get("status") == "active" and item.get("review_status") == "approved" and _knowledge_bool(item.get("retrieval_enabled")) and _curator_authority_role(item) == "authoritative_rule"]
        if len(active_authorities) > 1:
            insufficient = [item for item in active_authorities if len(_curator_applicability_key(item)) < 3]
            for item in insufficient:
                findings.append(_curator_finding(
                    "invalid_metadata", [item], reason_code="curator_missing_applicability", action="quarantine_for_review",
                    evidence_details={"missing_fields": ["applies_when"], "invalid_fields": [], "applicability_status": "insufficient_for_collision"},
                ))
        independent = [item for item in group if not item.get("supersedes_id")]
        if len(independent) > 1 and len({int(item.get("revision") or 1) for item in independent}) > 1:
            findings.append(_curator_finding(
                "apparently_superseded", independent, reason_code="curator_apparent_supersession", action="ask_owner",
                owner_question="Should the newer revision supersede the older record, or are both still required?",
            ))
    for group in by_collision.values():
        active = [item for item in group if item.get("status") == "active" and item.get("review_status") == "approved" and _knowledge_bool(item.get("retrieval_enabled"))]
        if len(active) > 1 and len({str(item.get("text") or "").strip() for item in active}) > 1:
            findings.append(_curator_finding(
                "incompatible_active_records", active, reason_code="curator_incompatible_active", action="ask_owner",
                owner_question="We found two different answers for the same customer situation. Which instruction should guide the next review?",
                evidence_details={"same_applicability": True, "applicability_status": "same_normalized_condition", "source_role": "authoritative_rule"},
            ))

    for predecessor, successors in children.items():
        if predecessor not in by_id:
            for child in successors:
                findings.append(_curator_finding("dangling_supersession", [child], reason_code="curator_dangling_supersession", action="quarantine_for_review"))
            continue
        parent = by_id[predecessor]
        if len(successors) > 1:
            findings.append(_curator_finding("branched_supersession", [parent, *successors], reason_code="curator_branched_supersession", action="ask_owner", owner_question="Which successor should replace the predecessor?"))
        for child in successors:
            if child.get("canonical_key") != parent.get("canonical_key"):
                findings.append(_curator_finding("cross_topic_supersession", [parent, child], reason_code="curator_cross_topic_supersession", action="quarantine_for_review"))
            if child.get("scope") != parent.get("scope"):
                findings.append(_curator_finding("cross_scope_supersession", [parent, child], reason_code="curator_cross_scope_supersession", action="quarantine_for_review"))

    # Cycle detection is path-local and emits one stable finding per cycle.
    emitted_cycles = set()
    for start in by_id:
        path: List[str] = []
        seen_at: Dict[str, int] = {}
        cursor = start
        while cursor in by_id:
            if cursor in seen_at:
                cycle_ids = tuple(sorted(path[seen_at[cursor]:]))
                if cycle_ids and cycle_ids not in emitted_cycles:
                    emitted_cycles.add(cycle_ids)
                    findings.append(_curator_finding("cyclic_supersession", [by_id[item_id] for item_id in cycle_ids], reason_code="curator_cyclic_supersession", action="quarantine_for_review"))
                break
            seen_at[cursor] = len(path)
            path.append(cursor)
            cursor = str(by_id[cursor].get("supersedes_id") or "")
            if not cursor:
                break

    # De-duplicate overlapping traversal output by stable fingerprint.
    return list({item["fingerprint"]: item for item in findings}.values())


def _classify_curator_model_failure(exc: Exception) -> str:
    """Classify a provider failure without retaining provider text or secrets."""
    code = _openai_error_code(exc)
    status_code = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
    name = type(exc).__name__.casefold()
    if is_openai_quota_exhausted(exc):
        return "openai_quota_exhausted"  # Kept for Phase 2.1 compatibility.
    if code in {"invalid_api_key", "invalid_authentication", "authentication_error", "unauthorized"} or status_code in {401, 403}:
        return "curator_model_authentication_failed"
    if code in {"model_not_found", "model_not_available", "unsupported_model", "access_denied"} or status_code == 404:
        return "curator_model_inaccessible"
    if code in {"rate_limit_exceeded", "rate_limited"} or status_code == 429:
        return "curator_model_rate_limited"
    if "timeout" in code or "timeout" in name or "timedout" in name:
        return "curator_model_timeout"
    return "curator_model_provider_error"


def _curator_owner_model_message(error_code: Optional[str]) -> str:
    messages = {
        "curator_model_not_configured": "The AI helper is not configured, but the safety checks completed successfully.",
        "openai_quota_exhausted": "The AI helper needs available credits, but the safety checks completed successfully.",
        "curator_model_inaccessible": "The AI helper is unavailable, but the safety checks completed successfully. Try again later.",
        "curator_model_authentication_failed": "The AI helper is unavailable, but the safety checks completed successfully. Try again later.",
        "curator_model_rate_limited": "The AI helper is busy, but the safety checks completed successfully. Try again later.",
        "curator_model_timeout": "The AI helper took too long, but the safety checks completed successfully. Try again later.",
        "curator_model_provider_error": "The AI helper was unavailable, but the safety checks completed successfully. Try again later.",
    }
    return messages.get(error_code or "", "Knowledge check completed.")


def _curator_enrich_proposals(findings: List[Dict[str, Any]]) -> tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    """Allow the model to refine safe proposals, never the underlying facts."""
    if not findings:
        return {}, None
    openai_client = get_main_attr("openai_client", None)
    if not openai_client:
        return {}, "curator_model_not_configured"
    safe_findings = [{
        "fingerprint": item["fingerprint"],
        "finding_type": item["finding_type"],
        "scope": item["scope"],
        "canonical_key": item["canonical_key"],
        "record_refs": item["records"],
        "reason_codes": item["reason_codes"],
        "allowed_default_action": item["proposed_action"],
    } for item in findings[:100]]
    instructions = (
        "You are a proposal-only knowledge curator. Return JSON with a proposals array. For each supplied "
        "fingerprint, preserve the fingerprint and choose only one of no_action, ask_owner, draft_replacement, "
        "draft_supersession, quarantine_for_review, merge_duplicate. Never decide which conflicting business "
        "fact is true. Conflicts must ask_owner. Never include prices, durations, dates, times, availability, "
        "customer data, URLs, secrets, source content, prompts or chain-of-thought. owner_questions must be a "
        "short array and confidence one of low, medium, high. Omit replacement text unless it only says to use "
        "current Settings values or check the live calendar."
    )
    try:
        request_client = (
            openai_client.with_options(max_retries=0, timeout=30)
            if callable(getattr(openai_client, "with_options", None))
            else openai_client
        )
        response = request_client.responses.create(
            model=get_main_attr("KNOWLEDGE_CURATOR_MODEL", KNOWLEDGE_CURATOR_MODEL),
            instructions=instructions,
            input=json.dumps({"findings": safe_findings}, ensure_ascii=False),
            store=False,
        )
        parsed = _parse_json_object(response.output_text or "").get("proposals", [])
    except Exception as exc:
        return {}, _classify_curator_model_failure(exc)
    allowed = {item["fingerprint"]: item for item in findings}
    enriched: Dict[str, Dict[str, Any]] = {}
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict) or str(item.get("fingerprint")) not in allowed:
            continue
        fingerprint = str(item["fingerprint"])
        # Deterministic analysis owns the action.  The model can improve only
        # confidence and safe owner questions; it cannot redirect a finding.
        action = allowed[fingerprint]["proposed_action"]
        questions = [
            str(value)[:500] for value in item.get("owner_questions", [])
            if isinstance(value, str)
            and not has_unsafe_literal_learning_detail(value)
            and not _curator_dynamic_claim_kind(value)
        ][:3]
        enriched[fingerprint] = {
            "proposed_action": action,
            "owner_questions": questions,
            "confidence": str(item.get("confidence")) if str(item.get("confidence")) in {"low", "medium", "high"} else "low",
        }
    return enriched, None


def run_knowledge_curator() -> Dict[str, Any]:
    """Run one bounded, idempotent, manual audit."""
    curator_lock = get_main_attr("KNOWLEDGE_CURATOR_LOCK", KNOWLEDGE_CURATOR_LOCK)
    if not curator_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A knowledge audit is already running.")
    try:
        started = datetime.utcnow().isoformat() + "Z"
        # A curator run is a manually initiated, proposal-only inspection.
        # It must never rewrite, merge, scope, supersede or activate production
        # knowledge merely because an operator requested a preview.
        maintenance = {
            "id": f"kcm-{uuid.uuid4()}", "timestamp": started,
            "result": "proposal_only", "repaired_count": 0, "repairs": [],
        }
        findings = inspect_knowledge_integrity()
        enrichments, error_code = _curator_enrich_proposals(findings)
        state = _load_curator_state()
        now_text = datetime.utcnow().isoformat() + "Z"
        by_fingerprint = {item.get("fingerprint"): item for item in state["proposals"]}
        observed_fingerprints = {item["fingerprint"] for item in findings}
        for prior in state["proposals"]:
            if prior.get("status") in KNOWLEDGE_CURATOR_UNRESOLVED_STATUSES and prior.get("fingerprint") not in observed_fingerprints:
                # Reconciliation closes only the curator card. It never edits
                # an existing record or any quarantined draft.
                prior["status"] = "resolved_no_longer_detected"
                prior["resolution"] = "dismiss_for_now"
                prior["updated_at"] = now_text
        created = 0
        for finding in findings:
            existing = by_fingerprint.get(finding["fingerprint"])
            if existing:
                existing["updated_at"] = now_text
                existing["last_seen_at"] = now_text
                continue
            proposal = {
                **finding,
                **enrichments.get(finding["fingerprint"], {}),
                "id": f"kcp-{finding['fingerprint'][:24]}",
                "status": "proposed",
                "created_at": now_text,
                "updated_at": now_text,
                "last_seen_at": now_text,
                "draft_entry_id": None,
            }
            state["proposals"].append(proposal)
            created += 1
        counts = dict(Counter(item["finding_type"] for item in findings))
        run = {
            "id": f"kcr-{uuid.uuid4()}",
            "status": "completed_with_warning" if error_code else "completed",
            "error_code": error_code,
            "message": _curator_owner_model_message(error_code),
            "started_at": started,
            "completed_at": now_text,
            "finding_counts": counts,
            "finding_count": len(findings),
            "created_proposals": created,
            "safe_repairs_completed": maintenance["repaired_count"],
            "ai_helper_status": "ready" if not error_code else error_code,
        }
        state["runs"].append(run)
        state.setdefault("maintenance_history", []).append(maintenance)
        _save_curator_state(state)
        return {"run": run, "proposals": _present_curator_state(state, unresolved_only=True)["proposals"]}
    finally:
        curator_lock.release()


def get_knowledge_curator_state() -> Dict[str, Any]:
    curator_lock = get_main_attr("KNOWLEDGE_CURATOR_LOCK", KNOWLEDGE_CURATOR_LOCK)
    with curator_lock:
        state = _load_curator_state()
    return _present_curator_state(state)


def _curator_proposal_is_current(proposal: Dict[str, Any]) -> bool:
    current = {str(item.get("id")): item for item in _curator_records()}
    return all(item.get("reference_status") == "current" for item in _curator_reference_statuses(proposal, current))


def transition_knowledge_curator_proposal(proposal_id: str, status_value: str) -> Dict[str, Any]:
    if status_value not in {"rejected", "dismissed"}:
        raise ValueError(status_value)
    curator_lock = get_main_attr("KNOWLEDGE_CURATOR_LOCK", KNOWLEDGE_CURATOR_LOCK)
    with curator_lock:
        state = _load_curator_state()
        proposal = next((item for item in state["proposals"] if item.get("id") == proposal_id), None)
        if not proposal:
            raise KeyError(proposal_id)
        if proposal.get("status") != "proposed":
            raise ValueError("Only unresolved proposed items can change state.")
        if not _curator_proposal_is_current(proposal):
            raise ValueError("The involved record revisions changed or are unavailable; run a fresh audit.")
        proposal["status"] = status_value
        proposal["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _save_curator_state(state)
        return dict(proposal)


def accept_knowledge_curator_proposal(proposal_id: str) -> Dict[str, Any]:
    """Backward-compatible safe-replacement action used by Phase 2 clients."""
    return resolve_knowledge_curator_proposal(proposal_id, "add_safe_replacement_draft")


def resolve_knowledge_curator_proposal(proposal_id: str, resolution: str, selected_record_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Resolve a card without ever mutating current knowledge authority."""
    if resolution not in KNOWLEDGE_CURATOR_RESOLUTIONS:
        raise ValueError("Unknown curator resolution.")
    selected = [str(value) for value in (selected_record_ids or []) if str(value).strip()]
    curator_lock = get_main_attr("KNOWLEDGE_CURATOR_LOCK", KNOWLEDGE_CURATOR_LOCK)
    with curator_lock:
        state = _load_curator_state()
        proposal = next((item for item in state["proposals"] if item.get("id") == proposal_id), None)
        if not proposal:
            raise KeyError(proposal_id)
        if proposal.get("status") == "accepted" and proposal.get("draft_entry_id") and resolution == "add_safe_replacement_draft":
            if not _curator_proposal_is_current(proposal):
                raise ValueError("The involved record revisions changed or are unavailable; run a fresh audit.")
            return dict(proposal)
        if proposal.get("status") != "proposed":
            raise ValueError("Only unresolved proposed items can be resolved.")
        if not _curator_proposal_is_current(proposal):
            raise ValueError("The involved record revisions changed; run a fresh audit.")
        refs = proposal.get("records", [])
        referenced_ids = {str(ref.get("id")) for ref in refs if isinstance(ref, dict)}
        if len(selected) != len(set(selected)) or any(item not in referenced_ids for item in selected):
            raise ValueError("Selected records must be current records referenced by this proposal.")
        if resolution == "select_current_rule" and len(selected) != 1:
            raise ValueError("Select exactly one current rule.")
        draft_resolutions = {"create_merged_draft", "create_consolidation_draft", "create_metadata_repair_draft", "add_safe_replacement_draft"}
        allowed_by_finding = {
            "incompatible_active_records": {"keep_all_examples", "select_current_rule", "create_merged_draft", "needs_manual_investigation", "not_an_issue", "dismiss_for_now"},
            "exact_duplicate": {"keep_both_distinct", "create_consolidation_draft", "needs_manual_investigation", "not_an_issue", "dismiss_for_now"},
            "invalid_metadata": {"create_metadata_repair_draft", "needs_manual_investigation", "not_an_issue", "dismiss_for_now"},
            "literal_dynamic_authority": {"add_safe_replacement_draft", "not_an_issue", "dismiss_for_now"},
        }
        allowed = allowed_by_finding.get(str(proposal.get("finding_type")), {"needs_manual_investigation", "not_an_issue", "dismiss_for_now"})
        if resolution not in allowed:
            raise ValueError("That resolution is not available for this finding.")

        now_text = datetime.utcnow().isoformat() + "Z"
        proposal["resolution"] = resolution
        proposal["selected_record_ids"] = selected
        if resolution not in draft_resolutions:
            proposal["status"] = "resolved_not_an_issue" if resolution == "not_an_issue" else "resolved"
            proposal["updated_at"] = now_text
            _save_curator_state(state)
            return dict(proposal)

        records = {str(item.get("id")): item for item in _curator_records()}
        predecessor_id = str(refs[0].get("id")) if refs else ""
        predecessor = records.get(predecessor_id)
        if not predecessor:
            raise ValueError("The proposed record no longer exists.")
        draft_data = proposal.get("replacement_draft") if isinstance(proposal.get("replacement_draft"), dict) else {}
        instruction_by_resolution = {
            "create_merged_draft": "Create a staff-reviewed merged rule for the referenced records. Do not activate it until the separate approval gate confirms the business rule.",
            "create_consolidation_draft": "Create a staff-reviewed consolidation draft for the referenced duplicate records. Do not activate it until separately approved.",
            "create_metadata_repair_draft": "Repair the required metadata for the referenced record, then submit it through staff review. Do not activate it automatically.",
        }
        instruction = str(draft_data.get("instruction") if resolution == "add_safe_replacement_draft" else instruction_by_resolution.get(resolution, ""))[:2000]
        if not instruction:
            raise ValueError("This finding has no safe replacement draft.")
        if _curator_dynamic_claim_kind(instruction):
            raise ValueError("A curator draft cannot contain a literal dynamic value.")
        entry_id = f"curator-{proposal_id}"
        entry = {
            "id": entry_id,
            "type": "curator_proposal",
            "source_type": "curator_proposal",
            "canonical_key": proposal.get("canonical_key"),
            "sms_account_key": proposal.get("scope"),
            "scope": proposal.get("scope"),
            "topic": str(draft_data.get("topic") or proposal.get("canonical_key") or "Knowledge review")[:500],
            "instruction": instruction,
            "text": instruction,
            "created_at": now_text,
            "updated_at": now_text,
            "status": "quarantined",
            # A pending draft must not revoke live knowledge.  The actual
            # supersedes edge is written only in the final atomic approval.
            "supersedes_id": None,
            "proposed_supersedes_id": predecessor_id if resolution == "add_safe_replacement_draft" else None,
            "revision": int(predecessor.get("revision") or 1) + 1,
            "version": int(predecessor.get("revision") or 1) + 1,
            "review_status": "pending",
            "retrieval_enabled": False,
            "review_source": "knowledge-curator",
            "curator_proposal_id": proposal_id,
            "curator_fingerprint": proposal.get("fingerprint"),
        }
        upsert_entry = get_main_attr("_upsert_learned_information_entry")
        if callable(upsert_entry):
            upsert_entry(entry)
        proposal["status"] = "accepted"
        proposal["draft_entry_id"] = entry_id
        proposal["updated_at"] = now_text
        _save_curator_state(state)
        return dict(proposal)


class KnowledgeCuratorService:
    """Unified service interface for the Knowledge Curator subsystem."""

    @staticmethod
    def inspect_integrity(
        records: Optional[List[Dict[str, Any]]] = None,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        return inspect_knowledge_integrity(records=records, now=now)

    @staticmethod
    def get_state() -> Dict[str, Any]:
        return get_knowledge_curator_state()

    @staticmethod
    def run() -> Dict[str, Any]:
        return run_knowledge_curator()

    @staticmethod
    def accept_proposal(proposal_id: str) -> Dict[str, Any]:
        return accept_knowledge_curator_proposal(proposal_id)

    @staticmethod
    def resolve_proposal(
        proposal_id: str,
        resolution: str,
        selected_record_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return resolve_knowledge_curator_proposal(proposal_id, resolution, selected_record_ids)

    @staticmethod
    def transition_proposal(proposal_id: str, status_value: str) -> Dict[str, Any]:
        return transition_knowledge_curator_proposal(proposal_id, status_value)

    @staticmethod
    def repair_legacy_metadata() -> Dict[str, Any]:
        from .supersession import _repair_legacy_knowledge_metadata
        return _repair_legacy_knowledge_metadata()

    @staticmethod
    def get_records() -> List[Dict[str, Any]]:
        return _curator_records()

    @staticmethod
    def is_quota_exhausted(exc: Exception) -> bool:
        return is_openai_quota_exhausted(exc)

    @staticmethod
    def classify_model_failure(exc: Exception) -> str:
        return _classify_curator_model_failure(exc)



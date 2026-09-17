"""Knowledge Authority subsystem: normalization, reason codes, and deterministic authority resolution."""

from collections import defaultdict
from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, List, Optional
import uuid

LEARNED_INFORMATION_FILENAME = "learned_information.jsonl"
LEARNING_REVIEW_STATUSES = {"pending", "approved"}
KNOWLEDGE_RECORD_STATUSES = {"active", "superseded", "expired", "quarantined"}
KNOWLEDGE_REASON_CODES = {
    "knowledge_excluded_unapproved",
    "knowledge_excluded_status",
    "knowledge_excluded_effective_range",
    "knowledge_excluded_retrieval_disabled",
    "knowledge_excluded_account",
    "knowledge_excluded_invalid",
    "knowledge_excluded_superseded",
    "knowledge_excluded_conflict",
    "knowledge_authority_overridden",
}
# This deliberately retains metadata only. It is operational evidence, not a
# transcript, prompt cache, or a place for customer information.
KNOWLEDGE_AUTHORITY_EVENTS: List[Dict[str, str]] = []


def _knowledge_reason(code: str, record_id: str = "") -> None:
    """Record a bounded, non-sensitive reason for an authority decision."""
    if code not in KNOWLEDGE_REASON_CODES:
        return
    KNOWLEDGE_AUTHORITY_EVENTS.append({"code": code, "record_id": record_id[:160]})
    del KNOWLEDGE_AUTHORITY_EVENTS[:-500]


def knowledge_authority_reason_codes() -> List[Dict[str, str]]:
    """Return a copy of the bounded structured audit trail for diagnostics."""
    return [dict(item) for item in KNOWLEDGE_AUTHORITY_EVENTS]


def _knowledge_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().casefold() in {"true", "1", "yes", "on"}:
            return True
        if value.strip().casefold() in {"false", "0", "no", "off", ""}:
            return False
    return default if value is None else bool(value)


def _knowledge_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Effective dates are an instant, not wall-clock text. Normalise offset
        # timestamps to naive UTC because the rest of this persistence layer
        # deliberately stores UTC-naive datetimes.
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return None


def _knowledge_timestamp_text(value: Any, fallback: str = "1970-01-01T00:00:00Z") -> str:
    parsed = _knowledge_timestamp(value)
    return (parsed.isoformat() + "Z") if parsed else fallback


def _canonical_knowledge_key(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return normalized[:160]


def normalize_knowledge_record(
    record: Dict[str, Any],
    *,
    source: str,
    source_index: int = 0,
    learned: bool = False,
) -> Dict[str, Any]:
    """Add versioned knowledge metadata in memory without rewriting source files.

    Old JSONL and uploaded text have no schema contract. Their defaults retain
    historic readability, while learned material remains fail-closed as before.
    """
    raw = dict(record) if isinstance(record, dict) else {"text": str(record or "")}
    text = str(raw.get("text") or raw.get("content") or raw.get("body") or raw.get("answer") or raw.get("question") or "").strip()
    stable_material = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    record_id = str(raw.get("id") or raw.get("record_id") or "").strip()
    if not record_id:
        record_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"assistant-ui:knowledge:{source}:{source_index}:{stable_material}"))
    source_type = str(raw.get("source_type") or raw.get("type") or "uploaded_knowledge").strip() or "uploaded_knowledge"
    legacy_upload = (
        not learned
        and source != "memory"
        and source_type in {"uploaded_text", "uploaded_knowledge"}
        and not raw.get("review_status")
    )
    scope = str(raw.get("sms_account_key") or raw.get("source_account_key") or raw.get("scope") or (
        "internal" if legacy_upload else "shared"
    )).strip().casefold()
    if scope not in {"primary", "secondary", "shared"}:
        scope = "internal"
    status = str(raw.get("status") or ("quarantined" if legacy_upload else "active")).strip().casefold()
    if status not in KNOWLEDGE_RECORD_STATUSES:
        status = "quarantined"
    review_status = str(raw.get("review_status") or (
        "pending" if legacy_upload else "approved" if learned and _knowledge_bool(raw.get("retrieval_enabled")) else "pending" if learned else "approved"
    )).strip().casefold()
    retrieval_default = False if legacy_upload or learned else True
    retrieval_enabled = _knowledge_bool(raw.get("retrieval_enabled"), retrieval_default)
    canonical_key = _canonical_knowledge_key(
        raw.get("canonical_key") or raw.get("key") or raw.get("topic") or text
    )
    try:
        revision = max(1, int(raw.get("revision") or raw.get("version") or 1))
    except (TypeError, ValueError):
        revision = 1
    normalized = {
        **raw,
        "id": record_id,
        "record_id": record_id,
        "canonical_key": canonical_key,
        "sms_account_key": scope,
        "scope": scope,
        "source_type": source_type,
        "source": source,
        "text": text,
        "created_at": _knowledge_timestamp_text(raw.get("created_at")),
        "updated_at": _knowledge_timestamp_text(raw.get("updated_at") or raw.get("created_at")),
        "effective_from": raw.get("effective_from") or raw.get("effectiveFrom"),
        "effective_until": raw.get("effective_until") or raw.get("effectiveUntil"),
        "status": status,
        "supersedes_id": str(raw.get("supersedes_id") or raw.get("supersedesId") or "").strip() or None,
        "revision": revision,
        "version": revision,
        "review_status": review_status,
        "retrieval_enabled": retrieval_enabled,
    }
    return normalized


normalise_knowledge_record = normalize_knowledge_record


def _knowledge_base_reason(record: Dict[str, Any], account_key: str, now: datetime) -> Optional[str]:
    if not record.get("id") or not record.get("canonical_key") or not record.get("text"):
        return "knowledge_excluded_invalid"
    if record.get("review_status") != "approved":
        return "knowledge_excluded_unapproved"
    if record.get("status") != "active":
        return "knowledge_excluded_status"
    if not _knowledge_bool(record.get("retrieval_enabled")):
        return "knowledge_excluded_retrieval_disabled"
    if record.get("sms_account_key") not in {account_key, "shared"}:
        return "knowledge_excluded_account"
    effective_from = _knowledge_timestamp(record.get("effective_from"))
    effective_until = _knowledge_timestamp(record.get("effective_until"))
    if (record.get("effective_from") and not effective_from) or (record.get("effective_until") and not effective_until):
        return "knowledge_excluded_invalid"
    if effective_from and now < effective_from or effective_until and now > effective_until:
        return "knowledge_excluded_effective_range"
    return None


def resolve_knowledge_authority(
    records: List[Dict[str, Any]],
    account_key: str = "primary",
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return only one deterministic, current authority per topic/account.

    A successor is a revocation boundary: if it is expired or malformed, its
    predecessor cannot reappear. This deliberately fails closed for dangling,
    cyclic and cross-account chains.
    """
    current = _knowledge_timestamp(now or datetime.utcnow()) or datetime.utcnow()
    normalized = [
        normalize_knowledge_record(item, source=str(item.get("source") or "memory"), source_index=index,
                                   learned=str(item.get("source") or "") == LEARNED_INFORMATION_FILENAME)
        for index, item in enumerate(records) if isinstance(item, dict)
    ]
    by_id: Dict[str, Dict[str, Any]] = {}
    duplicate_ids = set()
    for item in normalized:
        if item["id"] in by_id:
            duplicate_ids.add(item["id"])
        by_id[item["id"]] = item
    children: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        if item.get("supersedes_id"):
            children[item["supersedes_id"]].append(item)

    base_reasons: Dict[str, str] = {}
    for item in normalized:
        reason = _knowledge_base_reason(item, account_key, current)
        if item["id"] in duplicate_ids:
            reason = "knowledge_excluded_invalid"
        if reason:
            base_reasons[item["id"]] = reason

    # Supersession is meaningful only within one canonical fact. Evaluate an
    # entire connected component, rather than only an offending edge: otherwise
    # a descendant can escape a bad cross-topic edge or a branched revision.
    neighbours: Dict[str, set[str]] = defaultdict(set)
    for predecessor_id, successors in children.items():
        if predecessor_id not in by_id:
            continue
        for child in successors:
            neighbours[predecessor_id].add(child["id"])
            neighbours[child["id"]].add(predecessor_id)
    visited_components = set()
    for record_id in by_id:
        if record_id in visited_components:
            continue
        component, stack = set(), [record_id]
        while stack:
            node_id = stack.pop()
            if node_id in component:
                continue
            component.add(node_id)
            stack.extend(neighbours.get(node_id, set()) - component)
        visited_components.update(component)
        component_invalid = False
        component_conflict = False
        for node_id in component:
            successors = children.get(node_id, [])
            if len(successors) > 1:
                component_conflict = True
            for child in successors:
                if child.get("canonical_key") != by_id[node_id].get("canonical_key"):
                    component_invalid = True
                if child.get("sms_account_key") != by_id[node_id].get("sms_account_key"):
                    component_invalid = True
        if component_invalid or component_conflict:
            reason = "knowledge_excluded_invalid" if component_invalid else "knowledge_excluded_conflict"
            for node_id in component:
                base_reasons[node_id] = reason

    selected: List[Dict[str, Any]] = []
    processed = set()
    for item in normalized:
        item_id = item["id"]
        if item_id in processed:
            continue
        # Follow all descendants. A malformed edge invalidates every record
        # whose authority would otherwise rely on that chain.
        descendants: List[Dict[str, Any]] = []
        stack = [item]
        path = set()
        chain_invalid = False
        while stack:
            node = stack.pop()
            node_id = node["id"]
            if node_id in path:
                chain_invalid = True
                continue
            path.add(node_id)
            descendants.append(node)
            for child in children.get(node_id, []):
                if child.get("sms_account_key") != node.get("sms_account_key"):
                    chain_invalid = True
                stack.append(child)
        processed.update(path)
        if chain_invalid:
            for node in descendants:
                base_reasons[node["id"]] = "knowledge_excluded_invalid"
            continue
        # A missing predecessor is not a revision chain we can safely trust.
        for node in descendants:
            predecessor = node.get("supersedes_id")
            if predecessor and predecessor not in by_id:
                base_reasons[node["id"]] = "knowledge_excluded_invalid"
        leaves = [node for node in descendants if not children.get(node["id"])]
        # Any successor suppresses the predecessor, even an expired successor.
        for node in descendants:
            if children.get(node["id"]):
                base_reasons[node["id"]] = "knowledge_excluded_superseded"
        valid_leaves = [node for node in leaves if node["id"] not in base_reasons]
        if valid_leaves:
            newest = max(valid_leaves, key=lambda node: (
                node.get("revision", 1), _knowledge_timestamp(node.get("updated_at")) or datetime.min, node["id"]
            ))
            selected.append(newest)

    # Independent active records for one fact are a business conflict. Never
    # ask the model to pick a price, duration, policy, or wording from them.
    by_key: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for item in selected:
        by_key[(str(item.get("sms_account_key")), str(item.get("canonical_key")))].append(item)
    allowed_ids = set()
    for group in by_key.values():
        fingerprints = {str(item.get("text") or "") for item in group}
        if len(group) > 1 and len(fingerprints) > 1:
            for item in group:
                base_reasons[item["id"]] = "knowledge_excluded_conflict"
        else:
            allowed_ids.update(item["id"] for item in group)

    result = []
    for item in normalized:
        reason = base_reasons.get(item["id"])
        if item["id"] not in allowed_ids and not reason:
            reason = "knowledge_excluded_superseded"
        if reason:
            _knowledge_reason(reason, item["id"])
            continue
        if item["id"] in allowed_ids:
            result.append(item)
    return result

"""Knowledge Curator supersession and metadata repair subsystem."""

import contextlib
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Dict, List, Optional
import uuid

from .authority import (
    KNOWLEDGE_RECORD_STATUSES,
    LEARNED_INFORMATION_FILENAME,
    _knowledge_timestamp,
    _knowledge_timestamp_text,
    normalize_knowledge_record,
    resolve_knowledge_authority,
)
from .compat import DEFAULT_LEARNED_INFORMATION_LOCK, get_main_attr
from .sanitizer import _curator_dynamic_claim_kind
from .service import (
    KNOWLEDGE_CURATOR_LOCK,
    KNOWLEDGE_CURATOR_MAX_BACKUPS,
    _curator_authority_role,
    _curator_proposal_is_current,
    _curator_valid_revision,
    _load_curator_state,
    _save_curator_state,
)

_DEFAULT_KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")

def _curator_effective_record_snapshot(record: Dict[str, Any], *, source_index: int) -> Dict[str, Any]:
    """The behaviour contract a metadata-only repair must preserve."""
    normalized = normalize_knowledge_record(
        record, source=LEARNED_INFORMATION_FILENAME, source_index=source_index, learned=True,
    )
    # Deliberately exclude technical timestamps and labels being persisted. All
    # customer-facing and retrieval-relevant behaviour must compare exactly.
    fields = (
        "text", "canonical_key", "scope", "sms_account_key", "source_type", "status",
        "review_status", "retrieval_enabled", "supersedes_id", "effective_from", "effective_until",
        "applies_when", "customer_message", "customer", "question", "approved_reply", "reply",
        "instruction", "example_reply", "owner_information", "owner_topic", "owner_guidance",
    )
    return {field: normalized.get(field) for field in fields}


def _curator_authority_snapshot(records: List[Dict[str, Any]]) -> Dict[str, List[tuple[str, int, str]]]:
    """Compare authority results for every customer-facing line before writing."""
    snapshot: Dict[str, List[tuple[str, int, str]]] = {}
    for account_key in ("primary",):
        resolved = resolve_knowledge_authority(records, account_key=account_key)
        snapshot[account_key] = [
            (str(item.get("id") or ""), int(item.get("revision") or 1), str(item.get("canonical_key") or ""))
            for item in resolved
        ]
    return snapshot


def _curator_legacy_timestamp(raw: Dict[str, Any]) -> str:
    """Use only a valid pre-existing update/import timestamp, never the current time."""
    for field in ("updated_at", "imported_at", "import_timestamp"):
        value = raw.get(field)
        if _knowledge_timestamp(value):
            return _knowledge_timestamp_text(value)
    return ""


def _curator_has_authority_collision(items: List[Dict[str, Any]], candidate: Dict[str, Any], *, source_index: int) -> bool:
    normalized = normalize_knowledge_record(candidate, source=LEARNED_INFORMATION_FILENAME, source_index=source_index, learned=True)
    for other_index, other in enumerate(items):
        if other_index == source_index or not isinstance(other, dict):
            continue
        other_normalized = normalize_knowledge_record(other, source=LEARNED_INFORMATION_FILENAME, source_index=other_index, learned=True)
        if (
            other_normalized.get("sms_account_key") == normalized.get("sms_account_key")
            and other_normalized.get("canonical_key") == normalized.get("canonical_key")
            and _curator_authority_role(other_normalized) == "authoritative_rule"
            and _curator_authority_role(normalized) == "authoritative_rule"
        ):
            return True
    return False


def _curator_metadata_repair_candidate(items: List[Dict[str, Any]], index: int) -> tuple[Optional[Dict[str, Any]], List[str]]:
    """Return an unambiguous behaviour-neutral legacy metadata repair only."""
    raw = items[index]
    if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
        return None, []
    normalized = normalize_knowledge_record(raw, source=LEARNED_INFORMATION_FILENAME, source_index=index, learned=True)
    candidate = dict(raw)
    repaired: List[str] = []
    # Never coerce an invalid supplied value. Only materialise exactly the
    # normaliser default that the record already uses in practice.
    if not str(raw.get("revision") or raw.get("version") or "").strip() and normalized.get("revision") == 1:
        candidate["revision"] = 1
        candidate["version"] = 1
        repaired.extend(["revision", "version"])
    if not str(raw.get("status") or "").strip() and normalized.get("status") in KNOWLEDGE_RECORD_STATUSES:
        candidate["status"] = normalized["status"]
        repaired.append("status")
    if not str(raw.get("canonical_key") or raw.get("key") or raw.get("topic") or "").strip() and normalized.get("canonical_key"):
        if not _curator_has_authority_collision(items, candidate, source_index=index):
            candidate["canonical_key"] = normalized["canonical_key"]
            repaired.append("canonical_key")
    existing_timestamp = _curator_legacy_timestamp(raw)
    if not str(raw.get("created_at") or "").strip() and existing_timestamp:
        candidate["created_at"] = existing_timestamp
        repaired.append("created_at")
    if not str(raw.get("updated_at") or "").strip() and _knowledge_timestamp(candidate.get("created_at")):
        candidate["updated_at"] = _knowledge_timestamp_text(candidate["created_at"])
        repaired.append("updated_at")
    if not repaired:
        return None, []
    if _curator_effective_record_snapshot(raw, source_index=index) != _curator_effective_record_snapshot(candidate, source_index=index):
        return None, []
    return candidate, repaired


def _curator_backup_directory(filepath: str) -> str:
    return f"{filepath}.curator-backups"


def _curator_bound_backups(directory: str) -> None:
    backups = sorted(Path(directory).glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old_backup in backups[KNOWLEDGE_CURATOR_MAX_BACKUPS:]:
        old_backup.unlink(missing_ok=True)


def _repair_legacy_knowledge_metadata() -> Dict[str, Any]:
    """Atomically persist only proven legacy labels, with rollback and audit data."""
    timestamp = datetime.utcnow().isoformat() + "Z"
    audit: Dict[str, Any] = {"id": f"kcm-{uuid.uuid4()}", "timestamp": timestamp, "result": "no_changes", "repaired_count": 0, "repairs": []}
    knowledge_dir = get_main_attr("KNOWLEDGE_DIR", _DEFAULT_KNOWLEDGE_DIR)
    filepath = os.path.join(knowledge_dir, LEARNED_INFORMATION_FILENAME)
    if not os.path.exists(filepath):
        return audit
    lock = get_main_attr("LEARNED_INFORMATION_LOCK", DEFAULT_LEARNED_INFORMATION_LOCK)
    with lock:
        original = Path(filepath).read_text(encoding="utf-8")
        lines = original.splitlines()
        records: List[Dict[str, Any]] = []
        positions: List[int] = []
        for position, line in enumerate(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
                positions.append(position)
        if len({str(item.get("id") or "") for item in records if str(item.get("id") or "")}) != len([item for item in records if str(item.get("id") or "")]):
            audit["result"] = "skipped_ambiguous"
            return audit
        before_authority = _curator_authority_snapshot(records)
        replacements: Dict[int, Dict[str, Any]] = {}
        for index, raw in enumerate(records):
            candidate, fields = _curator_metadata_repair_candidate(records, index)
            if not candidate:
                continue
            proposed_records = list(records)
            proposed_records[index] = candidate
            if before_authority != _curator_authority_snapshot(proposed_records):
                continue
            replacements[index] = candidate
            audit["repairs"].append({"record_id": str(raw["id"])[:160], "fields": fields})
        if not replacements:
            return audit
        updated_lines = list(lines)
        for index, candidate in replacements.items():
            updated_lines[positions[index]] = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        # Validate every line which the writer will own before replacing the
        # original. Pre-existing malformed legacy lines remain byte-for-byte.
        for index in replacements:
            parsed = json.loads(updated_lines[positions[index]])
            if not isinstance(parsed, dict) or _curator_effective_record_snapshot(parsed, source_index=index) != _curator_effective_record_snapshot(records[index], source_index=index):
                audit["result"] = "validation_failed"
                audit["repairs"] = []
                return audit
        backup_directory = _curator_backup_directory(filepath)
        os.makedirs(backup_directory, exist_ok=True)
        backup_path = os.path.join(backup_directory, f"{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex}.jsonl")
        shutil.copy2(filepath, backup_path)
        temporary = f"{filepath}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write("\n".join(updated_lines) + ("\n" if original.endswith("\n") else ""))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, filepath)
            reload_fn = get_main_attr("load_knowledge_base")
            if callable(reload_fn):
                reload_fn()
            _curator_bound_backups(backup_directory)
        except Exception:
            try:
                rollback_temp = f"{filepath}.{uuid.uuid4().hex}.rollback.tmp"
                shutil.copy2(backup_path, rollback_temp)
                os.replace(rollback_temp, filepath)
                with contextlib.suppress(Exception):
                    reload_fn = get_main_attr("load_knowledge_base")
                    if callable(reload_fn):
                        reload_fn()
            finally:
                Path(temporary).unlink(missing_ok=True)
            audit["result"] = "rolled_back"
            audit["repairs"] = []
            return audit
    audit["result"] = "completed"
    audit["repaired_count"] = len(audit["repairs"])
    return audit


_AUTONOMOUS_QUARANTINE_FINDINGS = {
    "invalid_metadata",
    "expired_record",
    "literal_dynamic_authority",
    "shared_provider_specific",
    "dangling_supersession",
    "cyclic_supersession",
    "cross_topic_supersession",
    "cross_scope_supersession",
    "branched_supersession",
    "incompatible_active_records",
    "apparently_superseded",
}


def _autonomous_maintenance_action(
    record: Dict[str, Any], *, action: str, finding_type: str,
) -> Optional[Dict[str, Any]]:
    """Return a retrieval-reducing change, never a new business instruction."""
    # A record which is already out of live retrieval needs no write.  This
    # makes repeated background passes idempotent and avoids needless backups.
    normalized = normalize_knowledge_record(record, source=LEARNED_INFORMATION_FILENAME, source_index=0, learned=True)
    if (
        normalized.get("status") != "active"
        and not normalized.get("retrieval_enabled")
    ):
        return None
    candidate = dict(record)
    if action == "deduplicate":
        candidate["status"] = "superseded"
    else:
        candidate["status"] = "quarantined"
    candidate["review_status"] = "approved"
    candidate["retrieval_enabled"] = False
    # This is deliberately a compact reason code, rather than source content
    # or a generated explanation.  It keeps the file useful for audit without
    # storing any extra customer or business data.
    candidate["review_note"] = f"autonomous_curator:{finding_type}"[:160]
    return candidate


def _record_autonomous_maintenance(audit: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a content-free maintenance receipt without affecting SMS work."""
    try:
        curator_lock = get_main_attr("KNOWLEDGE_CURATOR_LOCK", KNOWLEDGE_CURATOR_LOCK)
        with curator_lock:
            state = _load_curator_state()
            state.setdefault("maintenance_history", []).append(audit)
            _save_curator_state(state)
    except Exception as exc:
        # The knowledge file is already protected by its own backup/rollback;
        # an unavailable audit file must not undo a safe maintenance result.
        print(f"Autonomous knowledge maintenance audit failed: {type(exc).__name__}")
    return audit


def run_autonomous_knowledge_maintenance() -> Dict[str, Any]:
    """Safely maintain legacy learned knowledge without an operator workflow.

    The background curator may only repair neutral metadata or *remove* a
    record from customer retrieval.  It never chooses between business facts,
    writes a replacement, or promotes knowledge.  Every mutation has an
    on-volume backup and is rolled back if reload or validation fails.
    """
    timestamp = datetime.utcnow().isoformat() + "Z"
    audit: Dict[str, Any] = {
        "id": f"kcm-{uuid.uuid4()}",
        "timestamp": timestamp,
        "result": "no_changes",
        "repaired_count": 0,
        "repairs": [],
    }
    # These repairs only materialise defaults the authority resolver already
    # uses.  They have their own atomic backup/rollback path.
    metadata_audit = _repair_legacy_knowledge_metadata()
    audit["repairs"].extend(metadata_audit.get("repairs", []))

    knowledge_dir = get_main_attr("KNOWLEDGE_DIR", _DEFAULT_KNOWLEDGE_DIR)
    filepath = os.path.join(knowledge_dir, LEARNED_INFORMATION_FILENAME)
    if not os.path.exists(filepath):
        audit["result"] = metadata_audit.get("result", "no_changes")
        audit["repaired_count"] = len(audit["repairs"])
        return _record_autonomous_maintenance(audit)

    lock = get_main_attr("LEARNED_INFORMATION_LOCK", DEFAULT_LEARNED_INFORMATION_LOCK)
    with lock:
        original = Path(filepath).read_text(encoding="utf-8")
        lines = original.splitlines()
        records: List[Dict[str, Any]] = []
        positions: List[int] = []
        for position, line in enumerate(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
                positions.append(position)

        ids = [str(item.get("id") or "").strip() for item in records]
        if not records or not all(ids) or len(set(ids)) != len(ids):
            audit["result"] = "skipped_ambiguous" if records else metadata_audit.get("result", "no_changes")
            audit["repaired_count"] = len(audit["repairs"])
            return _record_autonomous_maintenance(audit)

        # Import locally: service imports this module for manual supersession,
        # so a top-level import would create a module-initialisation cycle.
        from .service import inspect_knowledge_integrity

        findings = inspect_knowledge_integrity(records=records)
        by_id = {record_id: index for index, record_id in enumerate(ids)}
        actions: Dict[str, tuple[str, str]] = {}
        for finding in findings:
            finding_type = str(finding.get("finding_type") or "")
            references = finding.get("records")
            finding_ids = [str(ref.get("id") or "").strip() for ref in references if isinstance(ref, dict)] if isinstance(references, list) else []
            if not finding_ids or any(record_id not in by_id for record_id in finding_ids):
                continue
            if finding_type == "exact_duplicate":
                # Identical text, scope and canonical key: retaining the
                # oldest record is deterministic and does not select a fact.
                keep_id = min(finding_ids, key=lambda record_id: (str(records[by_id[record_id]].get("created_at") or ""), record_id))
                for record_id in finding_ids:
                    if record_id != keep_id:
                        actions[record_id] = ("deduplicate", finding_type)
            elif finding_type in _AUTONOMOUS_QUARANTINE_FINDINGS:
                for record_id in finding_ids:
                    # Quarantine wins over deduplication if a record appears
                    # in overlapping integrity findings.
                    actions[record_id] = ("quarantine", finding_type)

        replacements: Dict[int, Dict[str, Any]] = {}
        for record_id, (action, finding_type) in actions.items():
            index = by_id[record_id]
            candidate = _autonomous_maintenance_action(records[index], action=action, finding_type=finding_type)
            if not candidate:
                continue
            replacements[index] = candidate
            audit["repairs"].append({"record_id": record_id[:160], "fields": [action, finding_type]})

        if not replacements:
            audit["result"] = "completed" if audit["repairs"] else metadata_audit.get("result", "no_changes")
            audit["repaired_count"] = len(audit["repairs"])
            return _record_autonomous_maintenance(audit)

        proposed_records = list(records)
        for index, candidate in replacements.items():
            proposed_records[index] = candidate
        # A maintenance pass must strictly reduce authority; it must never make
        # any record available that was not already available before the run.
        before = _curator_authority_snapshot(records)
        after = _curator_authority_snapshot(proposed_records)
        if any(set(after.get(key, [])) - set(before.get(key, [])) for key in after):
            audit["result"] = "validation_failed"
            audit["repairs"] = audit["repairs"][:len(metadata_audit.get("repairs", []))]
            audit["repaired_count"] = len(audit["repairs"])
            return _record_autonomous_maintenance(audit)

        updated_lines = list(lines)
        for index, candidate in replacements.items():
            updated_lines[positions[index]] = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        backup_directory = _curator_backup_directory(filepath)
        os.makedirs(backup_directory, exist_ok=True)
        backup_path = os.path.join(backup_directory, f"{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex}.jsonl")
        shutil.copy2(filepath, backup_path)
        temporary = f"{filepath}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write("\n".join(updated_lines) + ("\n" if original.endswith("\n") else ""))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, filepath)
            reload_fn = get_main_attr("load_knowledge_base")
            if callable(reload_fn):
                reload_fn()
            _curator_bound_backups(backup_directory)
        except Exception:
            try:
                rollback_temp = f"{filepath}.{uuid.uuid4().hex}.rollback.tmp"
                shutil.copy2(backup_path, rollback_temp)
                os.replace(rollback_temp, filepath)
                with contextlib.suppress(Exception):
                    reload_fn = get_main_attr("load_knowledge_base")
                    if callable(reload_fn):
                        reload_fn()
            finally:
                Path(temporary).unlink(missing_ok=True)
            audit["result"] = "rolled_back"
            audit["repairs"] = audit["repairs"][:len(metadata_audit.get("repairs", []))]
            audit["repaired_count"] = len(audit["repairs"])
            return _record_autonomous_maintenance(audit)

    audit["result"] = "completed"
    audit["repaired_count"] = len(audit["repairs"])
    return _record_autonomous_maintenance(audit)


def _find_curator_supersession_target(
    entry: Dict[str, Any],
    records: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Find the target predecessor record that an entry supersedes."""
    predecessor_id = str(entry.get("proposed_supersedes_id") or entry.get("supersedes_id") or "").strip()
    if not predecessor_id:
        return None
    if records is None:
        from .service import _curator_records
        records = _curator_records()
    for item in records:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == predecessor_id:
            return item
    return None


def _approve_curator_supersession_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Atomically activate a current curator draft and retire its predecessor."""
    proposal_id = str(entry.get("curator_proposal_id") or "")

    curator_lock = get_main_attr("KNOWLEDGE_CURATOR_LOCK", KNOWLEDGE_CURATOR_LOCK)
    with curator_lock:
        curator_state = _load_curator_state()
        proposal = next((item for item in curator_state["proposals"] if item.get("id") == proposal_id), None)
        proposal_copy = dict(proposal) if proposal else None
    if not proposal_copy or proposal_copy.get("status") != "accepted" or proposal_copy.get("draft_entry_id") != entry.get("id"):
        raise ValueError("The curator proposal is not an accepted current draft.")
    if proposal_copy.get("proposed_action") not in {"draft_replacement", "draft_supersession"} or not _curator_proposal_is_current(proposal_copy):
        raise ValueError("The curator proposal is stale or is not a supersession.")
    if entry.get("scope") not in {"shared", "primary"} or _curator_dynamic_claim_kind(str(entry.get("text") or "")):
        raise ValueError("The curator draft is not safe to activate.")

    knowledge_dir = get_main_attr("KNOWLEDGE_DIR", _DEFAULT_KNOWLEDGE_DIR)
    filepath = os.path.join(knowledge_dir, LEARNED_INFORMATION_FILENAME)
    predecessor_id = str(entry.get("proposed_supersedes_id") or "")
    approved_entry: Optional[Dict[str, Any]] = None
    lock = get_main_attr("LEARNED_INFORMATION_LOCK", DEFAULT_LEARNED_INFORMATION_LOCK)
    with lock:
        raw_lines = open(filepath, "r", encoding="utf-8").read().splitlines()
        decoded: List[Optional[Dict[str, Any]]] = []
        for raw_line in raw_lines:
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError:
                value = None
            decoded.append(value if isinstance(value, dict) else None)
        records = {str(item.get("id")): item for item in decoded if item and item.get("id")}
        successor = records.get(str(entry.get("id")))
        predecessor = records.get(predecessor_id)
        if not successor or not predecessor:
            raise ValueError("The curator supersession records are no longer present.")
        if successor.get("canonical_key") != predecessor.get("canonical_key") or successor.get("scope") != predecessor.get("scope"):
            raise ValueError("Curator supersession key or scope mismatch.")
        expected_revision = next((int(ref.get("revision") or 1) for ref in proposal_copy.get("records", []) if ref.get("id") == predecessor_id), None)
        if expected_revision is None or int(predecessor.get("revision") or 1) != expected_revision:
            raise ValueError("The predecessor revision changed after the audit.")
        competing = [item for item in records.values() if item.get("supersedes_id") == predecessor_id and item.get("id") != successor.get("id")]
        if competing or predecessor.get("status") != "active" or predecessor.get("review_status") != "approved":
            raise ValueError("The predecessor is stale or already has another successor.")
        now_text = datetime.utcnow().isoformat() + "Z"
        predecessor.update({"status": "superseded", "retrieval_enabled": False, "updated_at": now_text})
        successor.update({
            "status": "active",
            "review_status": "approved",
            "retrieval_enabled": True,
            "supersedes_id": predecessor_id,
            "updated_at": now_text,
        })
        approved_entry = dict(successor)
        output_lines = []
        for raw_line, item in zip(raw_lines, decoded):
            output_lines.append(json.dumps(item, ensure_ascii=False) if item is not None else raw_line)
        temporary = f"{filepath}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output_lines) + "\n")
        os.replace(temporary, filepath)
    reload_fn = get_main_attr("load_knowledge_base")
    if callable(reload_fn):
        reload_fn()
    curator_lock = get_main_attr("KNOWLEDGE_CURATOR_LOCK", KNOWLEDGE_CURATOR_LOCK)
    with curator_lock:
        current_state = _load_curator_state()
        current = next((item for item in current_state["proposals"] if item.get("id") == proposal_id), None)
        if current:
            current["status"] = "applied"
            current["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _save_curator_state(current_state)
    return approved_entry or entry


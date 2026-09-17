"""Phase 9: Staged Migration, Dual-Write, and Parity Verification.

GOVERNING RULES:
1. Do not perform a destructive cut-over. Use staged migration.
2. Stage A: Database schema and repository abstraction introduced (Phase 3). JSONL remains authoritative.
3. Stage B: Dual-write where practical. Compare database state against JSONL state.
4. Stage C: Consistency verification checking counts, active records, superseded records,
   graph topology, effective dates, retrieval eligibility, canonical keys.
5. Stage D: Database becomes authoritative; JSONL export remains available as backup/debug/export format.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from backend.knowledge.models import KnowledgeRecord
from backend.knowledge.repository import (
    KnowledgeRepository,
    _canonicalize_key,
    _parse_datetime,
    _to_bool,
)

logger = logging.getLogger(__name__)


class MigrationStage(str, Enum):
    """Migration stages for staged transition from JSONL to DB authority."""

    STAGE_A_JSONL_AUTHORITATIVE = "stage_a"
    STAGE_B_DUAL_WRITE = "stage_b"
    STAGE_C_CONSISTENCY_CHECK = "stage_c"
    STAGE_D_DB_AUTHORITATIVE = "stage_d"


@dataclass
class ParityVerificationReport:
    """Parity verification report comparing JSONL store with Database repository."""

    is_in_sync: bool
    jsonl_count: int
    db_count: int
    active_records_in_sync: bool
    supersession_topology_in_sync: bool
    discrepancies: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "is_in_sync": self.is_in_sync,
            "jsonl_count": self.jsonl_count,
            "db_count": self.db_count,
            "active_records_in_sync": self.active_records_in_sync,
            "supersession_topology_in_sync": self.supersession_topology_in_sync,
            "discrepancies": list(self.discrepancies),
            "details": dict(self.details),
        }


# Anti-PII patterns for dual-write failure sanitization
PHONE_PATTERN = re.compile(
    r"(?:\+?61|0)[2-478](?:[-.\s]?\d){8}\b"
    r"|\b(?:\+\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"
    r"|\b04\d{2}[-.\s]?\d{3}[-.\s]?\d{3}\b"
    r"|\b\+?\d{8,15}\b",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    re.IGNORECASE,
)
CARD_PATTERN = re.compile(
    r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
)


def _sanitize_failure_reason(ex: Optional[Exception]) -> str:
    """Sanitize exception message to remove SQL parameters, raw message bodies, and customer PII."""
    if ex is None:
        return "UnknownError"

    exc_cls = type(ex).__name__

    # If wrapped by SQLAlchemy DBAPIError or DB driver, prefer clean underlying error if available
    if hasattr(ex, "orig") and ex.orig is not None:
        msg = str(ex.orig)
    else:
        msg = str(ex)

    # Strip SQLAlchemy SQL statements, parameter dumps, and background hints
    msg = re.split(r"\[SQL:", msg)[0]
    msg = re.split(r"\[parameters:", msg)[0]
    msg = re.split(r"\(Background on this error", msg)[0]

    # Redact sensitive PII patterns
    msg = CARD_PATTERN.sub("[CARD_REDACTED]", msg)
    msg = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", msg)
    msg = PHONE_PATTERN.sub("[PHONE_REDACTED]", msg)

    # Collapse whitespace and newlines
    msg = re.sub(r"\s+", " ", msg).strip()

    # Cap length to prevent unredacted raw message bodies from trailing through
    max_len = 150
    if len(msg) > max_len:
        msg = msg[:max_len].rstrip() + "..."

    if not msg:
        return exc_cls
    if msg.startswith(f"{exc_cls}:"):
        return msg
    return f"{exc_cls}: {msg}"


class KnowledgeParityChecker:
    """Verifies consistency and parity between JSONL authoritative records and database."""

    @classmethod
    def verify_parity(
        cls,
        jsonl_path: str,
        repository: KnowledgeRepository,
        dual_write_manager: Optional["KnowledgeDualWriteManager"] = None,
    ) -> ParityVerificationReport:
        """Verify parity between JSONL store and KnowledgeRepository.

        Validates:
        1. Record count and IDs parity.
        2. Content / canonical_key / account_key alignment.
        3. Status parity (active, superseded, quarantined, etc.).
        4. Retrieval eligibility parity.
        5. Supersession graph topology (supersedes_id and successor relationships).
        6. Effective timestamps.
        """
        discrepancies: List[str] = []

        # 1. Load JSONL records
        jsonl_records: Dict[str, dict] = {}
        if os.path.exists(jsonl_path):
            with open(jsonl_path, "r", encoding="utf-8") as handle:
                for line_num, line in enumerate(handle, start=1):
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        raw = json.loads(line_str)
                    except Exception as ex:
                        discrepancies.append(
                            f"JSONL parse error on line {line_num}: {ex}"
                        )
                        continue

                    if not isinstance(raw, dict):
                        continue

                    content = str(
                        raw.get("text")
                        or raw.get("content")
                        or raw.get("answer")
                        or raw.get("owner_information")
                        or raw.get("question")
                        or ""
                    ).strip()
                    if not content:
                        continue

                    record_id = str(raw.get("id") or raw.get("record_id") or "").strip()
                    if not record_id:
                        record_id = str(
                            uuid.uuid5(uuid.NAMESPACE_URL, f"assistant-ui:jsonl:{line_str}")
                        )

                    jsonl_records[record_id] = raw
        else:
            discrepancies.append(f"JSONL file not found at '{jsonl_path}'")

        # 2. Load DB records
        db_records_list = repository.list_all()
        db_records: Dict[str, KnowledgeRecord] = {r.id: r for r in db_records_list}

        jsonl_count = len(jsonl_records)
        db_count = len(db_records)

        # 1. Record count and IDs parity
        if jsonl_count != db_count:
            discrepancies.append(
                f"Record count mismatch: JSONL has {jsonl_count} records, DB has {db_count} records"
            )

        jsonl_ids = set(jsonl_records.keys())
        db_ids = set(db_records.keys())

        missing_in_db = sorted(list(jsonl_ids - db_ids))
        for rid in missing_in_db:
            discrepancies.append(f"Record '{rid}' present in JSONL but missing in DB")

        extra_in_db = sorted(list(db_ids - jsonl_ids))
        for rid in extra_in_db:
            discrepancies.append(f"Record '{rid}' present in DB but missing in JSONL")

        common_ids = sorted(list(jsonl_ids & db_ids))

        # 2. Content / canonical_key / account_key alignment & 3. Status & 4. Retrieval & 6. Effective dates
        for rid in common_ids:
            jl = jsonl_records[rid]
            db_rec = db_records[rid]

            # Content alignment
            jl_content = str(
                jl.get("content")
                or jl.get("text")
                or jl.get("answer")
                or jl.get("owner_information")
                or jl.get("question")
                or ""
            ).strip()
            db_content = str(db_rec.content or "").strip()
            if jl_content != db_content:
                discrepancies.append(
                    f"Content mismatch for record '{rid}': JSONL='{jl_content}' vs DB='{db_content}'"
                )

            # Canonical key alignment
            jl_key = str(
                jl.get("canonical_key")
                or jl.get("key")
                or jl.get("topic")
                or _canonicalize_key(jl_content)
            ).strip()
            db_key = str(db_rec.canonical_key or "").strip()
            if jl_key != db_key:
                discrepancies.append(
                    f"Canonical key mismatch for record '{rid}': JSONL='{jl_key}' vs DB='{db_key}'"
                )

            # Account key alignment
            jl_acc = str(
                jl.get("account_key")
                or jl.get("sms_account_key")
                or jl.get("scope")
                or "primary"
            ).strip().casefold()
            if jl_acc not in {"primary", "secondary", "shared"}:
                jl_acc = "primary"
            db_acc = str(db_rec.account_key or "primary").strip().casefold()
            if jl_acc != db_acc:
                discrepancies.append(
                    f"Account key mismatch for record '{rid}': JSONL='{jl_acc}' vs DB='{db_acc}'"
                )

            # Status alignment
            jl_status = str(jl.get("status") or "active").strip().casefold()
            db_status = str(db_rec.status or "active").strip().casefold()
            if jl_status != db_status:
                discrepancies.append(
                    f"Status mismatch for record '{rid}': JSONL='{jl_status}' vs DB='{db_status}'"
                )

            # Retrieval eligibility alignment
            jl_retrieval = _to_bool(jl.get("retrieval_enabled"), False)
            db_retrieval = bool(db_rec.retrieval_enabled)
            if jl_retrieval != db_retrieval:
                discrepancies.append(
                    f"Retrieval eligibility mismatch for record '{rid}': JSONL={jl_retrieval} vs DB={db_retrieval}"
                )

            # Effective timestamps alignment
            jl_eff_from = _parse_datetime(jl.get("effective_from") or jl.get("effectiveFrom"))
            db_eff_from = _parse_datetime(db_rec.effective_from)
            if jl_eff_from != db_eff_from:
                discrepancies.append(
                    f"Effective from mismatch for record '{rid}': JSONL='{jl_eff_from}' vs DB='{db_eff_from}'"
                )

            jl_eff_until = _parse_datetime(jl.get("effective_until") or jl.get("effectiveUntil"))
            db_eff_until = _parse_datetime(db_rec.effective_until)
            if jl_eff_until != db_eff_until:
                discrepancies.append(
                    f"Effective until mismatch for record '{rid}': JSONL='{jl_eff_until}' vs DB='{db_eff_until}'"
                )

        # Active records parity check
        jsonl_active_ids = {
            rid for rid, r in jsonl_records.items()
            if str(r.get("status") or "active").strip().casefold() == "active"
        }
        db_active_ids = {
            rid for rid, r in db_records.items()
            if str(r.status or "active").strip().casefold() == "active"
        }
        active_records_in_sync = (jsonl_active_ids == db_active_ids)
        if not active_records_in_sync:
            active_only_jl = sorted(list(jsonl_active_ids - db_active_ids))
            active_only_db = sorted(list(db_active_ids - jsonl_active_ids))
            if active_only_jl:
                discrepancies.append(
                    f"Active in JSONL but inactive/missing in DB: {active_only_jl}"
                )
            if active_only_db:
                discrepancies.append(
                    f"Active in DB but inactive/missing in JSONL: {active_only_db}"
                )

        # 5. Supersession graph topology parity
        supersession_topology_in_sync = True

        # Check links on common records
        for rid in common_ids:
            jl = jsonl_records[rid]
            db_rec = db_records[rid]

            jl_sup = jl.get("supersedes_id") or jl.get("supersedesId") or None
            jl_sub = jl.get("superseded_by_id") or jl.get("supersededById") or None
            db_sup = db_rec.supersedes_id or None
            db_sub = db_rec.superseded_by_id or None

            if jl_sup != db_sup:
                supersession_topology_in_sync = False
                discrepancies.append(
                    f"Supersedes link mismatch for record '{rid}': JSONL='{jl_sup}' vs DB='{db_sup}'"
                )

            if jl_sub != db_sub:
                supersession_topology_in_sync = False
                discrepancies.append(
                    f"Superseded_by link mismatch for record '{rid}': JSONL='{jl_sub}' vs DB='{db_sub}'"
                )

        # Compare forward edges
        jl_forward_edges = {
            (r.get("supersedes_id") or r.get("supersedesId"), rid)
            for rid, r in jsonl_records.items()
            if (r.get("supersedes_id") or r.get("supersedesId"))
        }
        db_forward_edges = {
            (r.supersedes_id, r.id)
            for r in db_records.values()
            if r.supersedes_id
        }
        if jl_forward_edges != db_forward_edges:
            supersession_topology_in_sync = False
            discrepancies.append(
                f"Supersession forward edges mismatch: JSONL={sorted(list(jl_forward_edges))} vs DB={sorted(list(db_forward_edges))}"
            )

        # Compare backward edges
        jl_backward_edges = {
            (rid, r.get("superseded_by_id") or r.get("supersededById"))
            for rid, r in jsonl_records.items()
            if (r.get("superseded_by_id") or r.get("supersededById"))
        }
        db_backward_edges = {
            (r.id, r.superseded_by_id)
            for r in db_records.values()
            if r.superseded_by_id
        }
        if jl_backward_edges != db_backward_edges:
            supersession_topology_in_sync = False
            discrepancies.append(
                f"Superseded backward edges mismatch: JSONL={sorted(list(jl_backward_edges))} vs DB={sorted(list(db_backward_edges))}"
            )

        # Validate internal graph consistency in DB
        for rid, r in db_records.items():
            if r.supersedes_id:
                pred = db_records.get(r.supersedes_id)
                if not pred:
                    supersession_topology_in_sync = False
                    discrepancies.append(
                        f"Broken supersession topology in DB: record '{rid}' supersedes non-existent '{r.supersedes_id}'"
                    )
                else:
                    if pred.status != "superseded":
                        supersession_topology_in_sync = False
                        discrepancies.append(
                            f"Broken supersession topology in DB: predecessor '{pred.id}' status is '{pred.status}', expected 'superseded'"
                        )
                    if pred.superseded_by_id != rid:
                        supersession_topology_in_sync = False
                        discrepancies.append(
                            f"Broken supersession topology in DB: predecessor '{pred.id}' superseded_by_id is '{pred.superseded_by_id}', expected '{rid}'"
                        )

            if r.superseded_by_id:
                succ = db_records.get(r.superseded_by_id)
                if not succ:
                    supersession_topology_in_sync = False
                    discrepancies.append(
                        f"Broken supersession topology in DB: record '{rid}' superseded by non-existent '{r.superseded_by_id}'"
                    )
                else:
                    if succ.supersedes_id != rid:
                        supersession_topology_in_sync = False
                        discrepancies.append(
                            f"Broken supersession topology in DB: successor '{succ.id}' supersedes_id is '{succ.supersedes_id}', expected '{rid}'"
                        )
                if r.status != "superseded":
                    supersession_topology_in_sync = False
                    discrepancies.append(
                        f"Broken supersession topology in DB: record '{rid}' has superseded_by_id '{r.superseded_by_id}' but status is '{r.status}'"
                    )

        # Validate internal graph consistency in JSONL
        for rid, r in jsonl_records.items():
            s_id = r.get("supersedes_id") or r.get("supersedesId")
            if s_id:
                pred_jl = jsonl_records.get(s_id)
                if not pred_jl:
                    supersession_topology_in_sync = False
                    discrepancies.append(
                        f"Broken supersession topology in JSONL: record '{rid}' supersedes non-existent '{s_id}'"
                    )
                else:
                    pred_status = str(pred_jl.get("status") or "active").strip().casefold()
                    if pred_status != "superseded":
                        supersession_topology_in_sync = False
                        discrepancies.append(
                            f"Broken supersession topology in JSONL: predecessor '{s_id}' status is '{pred_status}', expected 'superseded'"
                        )

        # Overall sync determination
        is_in_sync = (
            len(discrepancies) == 0
            and active_records_in_sync
            and supersession_topology_in_sync
            and jsonl_count == db_count
        )

        details = {
            "jsonl_count": jsonl_count,
            "db_count": db_count,
            "jsonl_active_count": len(jsonl_active_ids),
            "db_active_count": len(db_active_ids),
            "missing_in_db": missing_in_db,
            "extra_in_db": extra_in_db,
            "common_count": len(common_ids),
            "discrepancy_count": len(discrepancies),
        }

        report = ParityVerificationReport(
            is_in_sync=is_in_sync,
            jsonl_count=jsonl_count,
            db_count=db_count,
            active_records_in_sync=active_records_in_sync,
            supersession_topology_in_sync=supersession_topology_in_sync,
            discrepancies=discrepancies,
            details=details,
        )

        if dual_write_manager is not None:
            if report.is_in_sync:
                dual_write_manager.record_reconciliation_success()
            else:
                with dual_write_manager._lock:
                    dual_write_manager.parity_status = "diverged"

        return report


class KnowledgeDualWriteManager:
    """Manages staged migration, dual-write mirroring, and synchronization between JSONL and database."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        stage: Union[MigrationStage, str] = MigrationStage.STAGE_A_JSONL_AUTHORITATIVE,
    ) -> None:
        """Initialize manager with repository and migration stage."""
        self.repository = repository
        self._stage = self._normalize_stage(stage)
        self._lock = threading.Lock()
        self.dual_write_failure_count: int = 0
        self.parity_status: str = "in_sync"
        self.last_failure_timestamp: Optional[str] = None
        self.last_failure_reason: Optional[str] = None
        self.diverged_operations_count: int = 0

    @staticmethod
    def _normalize_stage(stage: Union[MigrationStage, str]) -> str:
        if isinstance(stage, MigrationStage):
            return stage.value
        val = str(stage).strip().casefold()
        valid_stages = {s.value for s in MigrationStage}
        if val in valid_stages:
            return val
        raise ValueError(
            f"Invalid migration stage '{stage}'. Expected one of: {sorted(list(valid_stages))}"
        )

    def get_stage(self) -> str:
        """Return the current migration stage string."""
        return self._stage

    def set_stage(self, stage: Union[MigrationStage, str]) -> None:
        """Set the migration stage."""
        self._stage = self._normalize_stage(stage)

    def _record_failure(self, ex: Exception) -> None:
        """Record mirror write failure and update divergence telemetry."""
        with self._lock:
            self.dual_write_failure_count += 1
            self.diverged_operations_count += 1
            self.parity_status = "diverged"
            self.last_failure_timestamp = datetime.now(timezone.utc).isoformat()
            self.last_failure_reason = _sanitize_failure_reason(ex)

    def get_parity_telemetry(self) -> Dict[str, Any]:
        """Return status and observability telemetry for dual-write parity."""
        with self._lock:
            return {
                "parity_status": self.parity_status,
                "dual_write_failure_count": self.dual_write_failure_count,
                "last_failure_timestamp": self.last_failure_timestamp,
                "last_failure_reason": self.last_failure_reason,
                "diverged_operations_count": self.diverged_operations_count,
            }

    def record_reconciliation_success(self) -> None:
        """Reset parity divergence status following successful reconciliation or parity verification.

        Cumulative dual_write_failure_count is preserved for historical metrics.
        """
        with self._lock:
            self.parity_status = "in_sync"
            self.diverged_operations_count = 0

    def reconcile_with_parity_checker(
        self,
        parity_checker_or_report: Any,
        jsonl_path: Optional[str] = None,
    ) -> Optional[ParityVerificationReport]:
        """Reconcile divergence status with a ParityVerificationReport or KnowledgeParityChecker."""
        if isinstance(parity_checker_or_report, ParityVerificationReport):
            if parity_checker_or_report.is_in_sync:
                self.record_reconciliation_success()
            else:
                with self._lock:
                    self.parity_status = "diverged"
            return parity_checker_or_report

        if isinstance(parity_checker_or_report, str):
            path = parity_checker_or_report
            return KnowledgeParityChecker.verify_parity(path, self.repository, dual_write_manager=self)

        checker = parity_checker_or_report
        if jsonl_path is not None and hasattr(checker, "verify_parity"):
            return checker.verify_parity(jsonl_path, self.repository, dual_write_manager=self)

        return None

    def on_jsonl_record_written(
        self,
        record_dict: dict,
        evidence_data: Optional[dict] = None,
    ) -> Optional[KnowledgeRecord]:
        """Hook called when a record is written to authoritative JSONL store.

        If stage in {STAGE_B_DUAL_WRITE, STAGE_C_CONSISTENCY_CHECK, STAGE_D_DB_AUTHORITATIVE}:
        mirrors write into repository.save_record.
        Non-fatal exception handling in Stage B: logs error without failing the authoritative write.
        """
        if self._stage == MigrationStage.STAGE_A_JSONL_AUTHORITATIVE:
            return None

        data = dict(record_dict)
        ev = evidence_data or data.pop("evidence_data", None)

        if self._stage == MigrationStage.STAGE_B_DUAL_WRITE:
            try:
                return self.repository.save_record(data, evidence_data=ev)
            except Exception as ex:
                self._record_failure(ex)
                logger.error(
                    "[DUAL_WRITE_ALERT] PostgreSQL mirror failed - Parity diverged: %s",
                    self.last_failure_reason,
                )
                return None
        elif self._stage in {
            MigrationStage.STAGE_C_CONSISTENCY_CHECK,
            MigrationStage.STAGE_D_DB_AUTHORITATIVE,
        }:
            return self.repository.save_record(data, evidence_data=ev)

        return None

    def on_jsonl_supersession_applied(
        self,
        predecessor_id: str,
        successor_id: str,
    ) -> None:
        """Hook called when supersession is applied in authoritative JSONL store.

        Mirrors mark_superseded into repository.
        Non-fatal exception handling in Stage B: logs error without failing.
        """
        if self._stage == MigrationStage.STAGE_A_JSONL_AUTHORITATIVE:
            return

        if self._stage == MigrationStage.STAGE_B_DUAL_WRITE:
            try:
                self.repository.mark_superseded(predecessor_id, successor_id)
            except Exception as ex:
                self._record_failure(ex)
                logger.error(
                    "[DUAL_WRITE_ALERT] PostgreSQL mirror failed - Parity diverged: %s",
                    self.last_failure_reason,
                )
        elif self._stage in {
            MigrationStage.STAGE_C_CONSISTENCY_CHECK,
            MigrationStage.STAGE_D_DB_AUTHORITATIVE,
        }:
            self.repository.mark_superseded(predecessor_id, successor_id)

    def sync_all_from_jsonl(self, jsonl_path: str) -> int:
        """Perform bulk synchronization from JSONL into database."""
        return self.repository.sync_from_jsonl(jsonl_path)

    def export_all_to_jsonl(self, jsonl_path: str) -> int:
        """Dump canonical records from database back to JSONL for backup/audit."""
        return self.repository.export_to_jsonl(jsonl_path)

"""Unit tests for Phase 9: Staged Migration, Dual-Write, and Parity Verification."""

import concurrent.futures
from datetime import datetime, timezone
import json
import os
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.knowledge.migration import (
    KnowledgeDualWriteManager,
    KnowledgeParityChecker,
    MigrationStage,
    ParityVerificationReport,
)
from backend.knowledge.models import (
    Base,
    KnowledgeRecord,
)
from backend.knowledge.repository import KnowledgeRepository


@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def repo(db_session):
    """KnowledgeRepository instance bound to the test session."""
    return KnowledgeRepository(db_session)


@pytest.fixture
def sample_jsonl_records():
    """Sample valid knowledge records for testing."""
    return [
        {
            "id": "mig-rec-1",
            "canonical_key": "operating-hours",
            "content": "We are open Monday to Friday 9am to 5pm.",
            "sms_account_key": "primary",
            "status": "active",
            "retrieval_enabled": True,
            "authority_level": "canonical_knowledge",
            "revision": 1,
            "question": "What are your trading hours?",
            "created_at": "2026-01-01T09:00:00+00:00",
        },
        {
            "id": "mig-rec-2",
            "canonical_key": "parking-info",
            "content": "Complimentary customer parking is available at the rear.",
            "sms_account_key": "primary",
            "status": "active",
            "retrieval_enabled": True,
            "authority_level": "canonical_knowledge",
            "revision": 1,
            "question": "Where can I park?",
            "created_at": "2026-01-02T10:00:00+00:00",
        },
        {
            "id": "mig-rec-3",
            "canonical_key": "emergency-procedure",
            "content": "In case of emergency call triple zero immediately.",
            "sms_account_key": "shared",
            "status": "active",
            "retrieval_enabled": True,
            "authority_level": "owner_instruction",
            "revision": 1,
            "created_at": "2026-01-03T11:00:00+00:00",
        },
    ]


def test_1_sync_all_from_jsonl(tmp_path, repo, sample_jsonl_records):
    """Test 1: sync_all_from_jsonl loads JSONL data into database."""
    jsonl_file = tmp_path / "test_knowledge.jsonl"
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for r in sample_jsonl_records:
            f.write(json.dumps(r) + "\n")

    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_A_JSONL_AUTHORITATIVE)
    assert manager.get_stage() == "stage_a"

    count = manager.sync_all_from_jsonl(str(jsonl_file))
    assert count == 3

    # Verify records in database
    records = repo.list_all()
    assert len(records) == 3

    r1 = repo.get_by_id("mig-rec-1")
    assert r1 is not None
    assert r1.canonical_key == "operating-hours"
    assert r1.content == "We are open Monday to Friday 9am to 5pm."
    assert r1.account_key == "primary"
    assert r1.retrieval_enabled is True
    assert len(r1.evidence_items) == 1
    assert r1.evidence_items[0].original_text_reference == "What are your trading hours?"

    r3 = repo.get_by_id("mig-rec-3")
    assert r3 is not None
    assert r3.account_key == "shared"


def test_2_verify_parity_in_sync(tmp_path, repo, sample_jsonl_records):
    """Test 2: verify_parity returns is_in_sync=True when database and JSONL match."""
    jsonl_file = tmp_path / "in_sync.jsonl"
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for r in sample_jsonl_records:
            f.write(json.dumps(r) + "\n")

    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_A_JSONL_AUTHORITATIVE)
    manager.sync_all_from_jsonl(str(jsonl_file))

    report = KnowledgeParityChecker.verify_parity(str(jsonl_file), repo)

    assert isinstance(report, ParityVerificationReport)
    assert report.is_in_sync is True
    assert report.jsonl_count == 3
    assert report.db_count == 3
    assert report.active_records_in_sync is True
    assert report.supersession_topology_in_sync is True
    assert len(report.discrepancies) == 0
    assert report.details["discrepancy_count"] == 0

    # Serialization check
    d = report.to_dict()
    assert d["is_in_sync"] is True
    assert d["jsonl_count"] == 3


def test_3_verify_parity_detects_discrepancies(tmp_path, repo, sample_jsonl_records):
    """Test 3: verify_parity detects discrepancies (missing record, count mismatch, status mismatch, broken supersession)."""
    jsonl_file = tmp_path / "base.jsonl"
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for r in sample_jsonl_records:
            f.write(json.dumps(r) + "\n")

    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_A_JSONL_AUTHORITATIVE)
    manager.sync_all_from_jsonl(str(jsonl_file))

    # --- 3a. Missing record & Count mismatch in DB ---
    extra_record = {
        "id": "mig-rec-4",
        "canonical_key": "wifi-access",
        "content": "Guest wifi network is Guest-Net, password welcome.",
        "status": "active",
        "retrieval_enabled": True,
    }
    mismatch_jsonl = tmp_path / "mismatch_extra.jsonl"
    with open(mismatch_jsonl, "w", encoding="utf-8") as f:
        for r in sample_jsonl_records:
            f.write(json.dumps(r) + "\n")
        f.write(json.dumps(extra_record) + "\n")

    report_missing = KnowledgeParityChecker.verify_parity(str(mismatch_jsonl), repo)
    assert report_missing.is_in_sync is False
    assert report_missing.jsonl_count == 4
    assert report_missing.db_count == 3
    assert any("Record count mismatch" in d for d in report_missing.discrepancies)
    assert any("mig-rec-4" in d and "missing in DB" in d for d in report_missing.discrepancies)
    assert "mig-rec-4" in report_missing.details["missing_in_db"]

    # --- 3b. Status mismatch & Active records out of sync ---
    # In DB, update status of mig-rec-1 to superseded without updating JSONL
    r1 = repo.get_by_id("mig-rec-1")
    r1.status = "superseded"
    repo._get_session().commit()

    report_status = KnowledgeParityChecker.verify_parity(str(jsonl_file), repo)
    assert report_status.is_in_sync is False
    assert report_status.active_records_in_sync is False
    assert any("Status mismatch for record 'mig-rec-1'" in d for d in report_status.discrepancies)

    # Revert status
    r1.status = "active"
    repo._get_session().commit()

    # --- 3c. Broken supersession topology ---
    # Create successor record in DB that claims to supersede mig-rec-1,
    # but mig-rec-1 is still status="active" and has no superseded_by_id link
    repo.save_record({
        "id": "mig-rec-successor",
        "canonical_key": "operating-hours",
        "content": "New operating hours: 8am to 6pm.",
        "status": "active",
        "retrieval_enabled": True,
        "supersedes_id": "mig-rec-1",
    })

    # Also add to JSONL with supersedes link
    broken_jsonl = tmp_path / "broken_supersession.jsonl"
    with open(broken_jsonl, "w", encoding="utf-8") as f:
        for r in sample_jsonl_records:
            f.write(json.dumps(r) + "\n")
        f.write(json.dumps({
            "id": "mig-rec-successor",
            "canonical_key": "operating-hours",
            "content": "New operating hours: 8am to 6pm.",
            "status": "active",
            "retrieval_enabled": True,
            "supersedes_id": "mig-rec-1",
        }) + "\n")

    report_broken = KnowledgeParityChecker.verify_parity(str(broken_jsonl), repo)
    assert report_broken.is_in_sync is False
    assert report_broken.supersession_topology_in_sync is False
    assert any("Broken supersession topology in DB" in d for d in report_broken.discrepancies)


def test_4_dual_write_mirrors_upserts_and_supersession(repo):
    """Test 4: Dual-write manager mirrors record upserts and supersession links in Stage B."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)
    assert manager.get_stage() == "stage_b"

    rec1 = {
        "id": "dw-1",
        "canonical_key": "pricing-tier",
        "content": "Standard plan is $29/mo.",
        "sms_account_key": "primary",
        "status": "active",
        "retrieval_enabled": True,
        "authority_level": "canonical_knowledge",
    }
    saved1 = manager.on_jsonl_record_written(rec1)
    assert saved1 is not None
    assert saved1.id == "dw-1"
    assert repo.get_by_id("dw-1") is not None
    assert repo.get_by_id("dw-1").content == "Standard plan is $29/mo."

    rec2 = {
        "id": "dw-2",
        "canonical_key": "pricing-tier",
        "content": "Standard plan is updated to $35/mo.",
        "sms_account_key": "primary",
        "status": "active",
        "retrieval_enabled": True,
        "authority_level": "canonical_knowledge",
    }
    saved2 = manager.on_jsonl_record_written(rec2)
    assert saved2 is not None
    assert saved2.id == "dw-2"

    # Mirror supersession link
    manager.on_jsonl_supersession_applied(predecessor_id="dw-1", successor_id="dw-2")

    r1 = repo.get_by_id("dw-1")
    r2 = repo.get_by_id("dw-2")

    assert r1.status == "superseded"
    assert r1.superseded_by_id == "dw-2"
    assert r2.supersedes_id == "dw-1"


def test_5_dual_write_non_fatal_resilience_in_stage_b(repo):
    """Test 5: Dual-write non-fatal resilience in Stage B (database failure does not crash JSONL caller)."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)

    # Mock save_record to simulate DB failure
    original_save = repo.save_record
    repo.save_record = MagicMock(side_effect=RuntimeError("Simulated DB connection failure"))

    record = {
        "id": "resilient-1",
        "canonical_key": "resilience-test",
        "content": "This write should not fail the caller in Stage B.",
        "status": "active",
    }

    # Should not raise exception
    res = manager.on_jsonl_record_written(record)
    assert res is None
    repo.save_record.assert_called_once()

    # Mock mark_superseded to simulate DB timeout
    repo.mark_superseded = MagicMock(side_effect=RuntimeError("Simulated DB timeout"))

    # Should not raise exception
    manager.on_jsonl_supersession_applied("resilient-1", "resilient-2")
    repo.mark_superseded.assert_called_once()

    # Restore repo methods
    repo.save_record = original_save

    # Verify that in Stage D, failure is authoritative and raises
    manager.set_stage(MigrationStage.STAGE_D_DB_AUTHORITATIVE)
    repo.save_record = MagicMock(side_effect=RuntimeError("Authoritative DB write failed"))

    with pytest.raises(RuntimeError, match="Authoritative DB write failed"):
        manager.on_jsonl_record_written(record)


def test_6_staged_transition_flow_and_export(tmp_path, repo, sample_jsonl_records):
    """Test 6: Staged transition flow (Stage A -> Stage B -> Stage C -> Stage D) and export back to JSONL."""
    source_jsonl = tmp_path / "stage_flow.jsonl"
    with open(source_jsonl, "w", encoding="utf-8") as f:
        for r in sample_jsonl_records:
            f.write(json.dumps(r) + "\n")

    # Stage A: JSONL authoritative, DB empty
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_A_JSONL_AUTHORITATIVE)
    assert manager.get_stage() == "stage_a"

    # Stage A: on_jsonl_record_written is no-op
    no_op_write = manager.on_jsonl_record_written({
        "id": "ignore-stage-a",
        "content": "Should not write to DB in stage A",
    })
    assert no_op_write is None
    assert repo.get_by_id("ignore-stage-a") is None

    # Bulk sync from JSONL into DB
    synced_count = manager.sync_all_from_jsonl(str(source_jsonl))
    assert synced_count == 3
    assert len(repo.list_all()) == 3

    # Parity check at end of Stage A
    report_a = KnowledgeParityChecker.verify_parity(str(source_jsonl), repo)
    assert report_a.is_in_sync is True

    # Transition to Stage B: Dual Write
    manager.set_stage(MigrationStage.STAGE_B_DUAL_WRITE)
    assert manager.get_stage() == "stage_b"

    new_rec = {
        "id": "mig-rec-4",
        "canonical_key": "holidays",
        "content": "We are closed on national public holidays.",
        "sms_account_key": "primary",
        "status": "active",
        "retrieval_enabled": True,
        "authority_level": "canonical_knowledge",
    }
    # Append to JSONL
    with open(source_jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps(new_rec) + "\n")

    # Mirror to DB via dual-write hook
    manager.on_jsonl_record_written(new_rec)
    assert repo.get_by_id("mig-rec-4") is not None
    assert repo.get_by_id("mig-rec-4").content == "We are closed on national public holidays."

    # Transition to Stage C: Consistency Check
    manager.set_stage(MigrationStage.STAGE_C_CONSISTENCY_CHECK)
    assert manager.get_stage() == "stage_c"

    report_c = KnowledgeParityChecker.verify_parity(str(source_jsonl), repo)
    assert report_c.is_in_sync is True
    assert report_c.jsonl_count == 4
    assert report_c.db_count == 4
    assert len(report_c.discrepancies) == 0

    # Transition to Stage D: DB Authoritative
    manager.set_stage(MigrationStage.STAGE_D_DB_AUTHORITATIVE)
    assert manager.get_stage() == "stage_d"

    # Add record directly into DB (since DB is authoritative)
    repo.save_record({
        "id": "mig-rec-5",
        "canonical_key": "pet-policy",
        "content": "Guide dogs and certified assistance animals are welcome.",
        "account_key": "shared",
        "status": "active",
        "retrieval_enabled": True,
    })

    # Export back to JSONL for backup/audit
    export_jsonl = tmp_path / "authoritative_export.jsonl"
    exported_count = manager.export_all_to_jsonl(str(export_jsonl))
    assert exported_count == 5

    # Verify parity between exported JSONL and DB
    report_d = KnowledgeParityChecker.verify_parity(str(export_jsonl), repo)
    assert report_d.is_in_sync is True
    assert report_d.jsonl_count == 5
    assert report_d.db_count == 5
    assert len(report_d.discrepancies) == 0


def test_7_dual_write_success_maintains_in_sync(repo):
    """Test 1 (Requirement): Successful Stage B write keeps parity_status == 'in_sync', dual_write_failure_count == 0."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)
    assert manager.parity_status == "in_sync"
    assert manager.dual_write_failure_count == 0
    assert manager.diverged_operations_count == 0
    assert manager.last_failure_timestamp is None
    assert manager.last_failure_reason is None

    rec = {
        "id": "dw-sync-1",
        "canonical_key": "sync-test",
        "content": "Operating hours are 9am to 5pm.",
        "sms_account_key": "primary",
        "status": "active",
        "retrieval_enabled": True,
    }
    saved = manager.on_jsonl_record_written(rec)
    assert saved is not None
    assert saved.id == "dw-sync-1"
    assert repo.get_by_id("dw-sync-1") is not None

    telemetry = manager.get_parity_telemetry()
    assert telemetry["parity_status"] == "in_sync"
    assert telemetry["dual_write_failure_count"] == 0
    assert telemetry["diverged_operations_count"] == 0
    assert telemetry["last_failure_timestamp"] is None
    assert telemetry["last_failure_reason"] is None


def test_8_dual_write_record_failure_divergence_telemetry(repo):
    """Test 2 (Requirement): Database failure during record write increments dual_write_failure_count,
    sets parity_status == 'diverged', records UTC timestamp and sanitized reason,
    and does NOT throw an exception (JSONL succeeds)."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)

    repo.save_record = MagicMock(side_effect=RuntimeError("OperationalError: connection refused"))

    rec = {
        "id": "dw-fail-1",
        "canonical_key": "fail-test",
        "content": "Some knowledge content.",
        "status": "active",
    }

    # Must NOT throw an exception (JSONL operations continue uninterrupted)
    result = manager.on_jsonl_record_written(rec)
    assert result is None

    assert manager.parity_status == "diverged"
    assert manager.dual_write_failure_count == 1
    assert manager.diverged_operations_count == 1
    assert manager.last_failure_timestamp is not None

    # Validate ISO8601 UTC timestamp
    dt = datetime.fromisoformat(manager.last_failure_timestamp)
    assert dt.tzinfo is not None
    assert "connection refused" in manager.last_failure_reason

    telemetry = manager.get_parity_telemetry()
    assert telemetry["parity_status"] == "diverged"
    assert telemetry["dual_write_failure_count"] == 1
    assert telemetry["diverged_operations_count"] == 1
    assert telemetry["last_failure_timestamp"] == manager.last_failure_timestamp
    assert telemetry["last_failure_reason"] == manager.last_failure_reason


def test_9_dual_write_supersession_failure_divergence_telemetry(repo):
    """Test 3 (Requirement): Database failure during supersession write increments failure count and marks status diverged."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)

    repo.mark_superseded = MagicMock(side_effect=RuntimeError("Database lock timeout"))

    # Must NOT throw an exception
    manager.on_jsonl_supersession_applied("pred-1", "succ-1")

    assert manager.parity_status == "diverged"
    assert manager.dual_write_failure_count == 1
    assert manager.diverged_operations_count == 1
    assert manager.last_failure_timestamp is not None
    assert "Database lock timeout" in manager.last_failure_reason

    telemetry = manager.get_parity_telemetry()
    assert telemetry["parity_status"] == "diverged"
    assert telemetry["dual_write_failure_count"] == 1
    assert telemetry["diverged_operations_count"] == 1


def test_10_dual_write_repeated_failures_increment_counts(repo):
    """Test 4 (Requirement): Repeated failures increment failure and diverged operation counts."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)

    repo.save_record = MagicMock(side_effect=RuntimeError("DB error 1"))
    repo.mark_superseded = MagicMock(side_effect=RuntimeError("DB error 2"))

    manager.on_jsonl_record_written({"id": "rec-1", "content": "content 1"})
    manager.on_jsonl_record_written({"id": "rec-2", "content": "content 2"})
    manager.on_jsonl_supersession_applied("rec-1", "rec-2")

    assert manager.dual_write_failure_count == 3
    assert manager.diverged_operations_count == 3
    assert manager.parity_status == "diverged"
    assert "DB error 2" in manager.last_failure_reason

    telemetry = manager.get_parity_telemetry()
    assert telemetry["dual_write_failure_count"] == 3
    assert telemetry["diverged_operations_count"] == 3


def test_11_dual_write_reconciliation_resets_parity_status(tmp_path, repo, sample_jsonl_records):
    """Test 5 (Requirement): Running parity reconciliation / verify_parity that succeeds resets parity_status
    back to 'in_sync' and resets diverged_operations_count."""
    jsonl_file = tmp_path / "reconcile.jsonl"
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for r in sample_jsonl_records:
            f.write(json.dumps(r) + "\n")

    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)

    # 1. Simulate failure leading to diverged status
    original_save = repo.save_record
    repo.save_record = MagicMock(side_effect=RuntimeError("Transient network failure"))
    manager.on_jsonl_record_written({"id": "failed-rec", "content": "transient"})
    assert manager.parity_status == "diverged"
    assert manager.dual_write_failure_count == 1
    assert manager.diverged_operations_count == 1

    # Restore save_record to allow successful reconciliation sync
    repo.save_record = original_save

    # 2. Synchronize database to parity
    manager.sync_all_from_jsonl(str(jsonl_file))

    # 3. verify_parity with dual_write_manager attached reconciles status
    report = KnowledgeParityChecker.verify_parity(str(jsonl_file), repo, dual_write_manager=manager)
    assert report.is_in_sync is True

    # Parity status must be reset to in_sync and diverged_operations_count to 0
    assert manager.parity_status == "in_sync"
    assert manager.diverged_operations_count == 0
    # Cumulative failure count should be retained for historical observability
    assert manager.dual_write_failure_count == 1

    telemetry = manager.get_parity_telemetry()
    assert telemetry["parity_status"] == "in_sync"
    assert telemetry["diverged_operations_count"] == 0
    assert telemetry["dual_write_failure_count"] == 1

    # 4. Also test reconcile_with_parity_checker directly with report
    manager.parity_status = "diverged"
    manager.diverged_operations_count = 5
    manager.reconcile_with_parity_checker(report)
    assert manager.parity_status == "in_sync"
    assert manager.diverged_operations_count == 0

    # 5. Also test reconcile_with_parity_checker with KnowledgeParityChecker class
    manager.parity_status = "diverged"
    manager.diverged_operations_count = 2
    manager.reconcile_with_parity_checker(KnowledgeParityChecker, jsonl_path=str(jsonl_file))
    assert manager.parity_status == "in_sync"
    assert manager.diverged_operations_count == 0


def test_12_dual_write_failure_anti_pii_sanitization(repo):
    """Test 6 (Requirement): Verify no customer PII leaks into last_failure_reason."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)

    customer_phone_e164 = "+61412345678"
    customer_phone_local = "0412 987 654"
    customer_email = "jane.doe@customer-secret-domain.com"
    customer_card = "4532-1234-5678-9012"
    raw_message_body = "CONFIDENTIAL: Customer request regarding medical booking at 0412345678"

    # Simulate exception containing SQL dump and customer PII
    pii_exception_message = (
        f"OperationalError: database write failed for user phone {customer_phone_e164}, "
        f"alt {customer_phone_local}, email {customer_email}, card {customer_card} "
        f"[SQL: INSERT INTO knowledge_records (content) VALUES ('{raw_message_body}')] "
        f"[parameters: {{'phone': '{customer_phone_e164}', 'body': '{raw_message_body}'}}]"
    )

    repo.save_record = MagicMock(side_effect=RuntimeError(pii_exception_message))

    manager.on_jsonl_record_written({
        "id": "pii-rec",
        "content": raw_message_body,
    })

    assert manager.parity_status == "diverged"
    reason = manager.last_failure_reason
    assert reason is not None

    # Assert no customer PII leaks into sanitized failure reason
    assert customer_phone_e164 not in reason
    assert customer_phone_local not in reason
    assert customer_email not in reason
    assert customer_card not in reason
    assert raw_message_body not in reason
    assert "[SQL:" not in reason
    assert "[parameters:" not in reason

    # Check that redaction markers are used appropriately
    assert "[PHONE_REDACTED]" in reason
    assert "[EMAIL_REDACTED]" in reason
    assert "[CARD_REDACTED]" in reason

    # Verify telemetry dictionary also contains sanitized reason
    telemetry = manager.get_parity_telemetry()
    assert telemetry["last_failure_reason"] == reason


def test_13_dual_write_concurrent_failures_thread_safety(repo):
    """Test 7 (Requirement): Thread-safe telemetry updates under simultaneous concurrent failures."""
    manager = KnowledgeDualWriteManager(repo, stage=MigrationStage.STAGE_B_DUAL_WRITE)

    repo.save_record = MagicMock(side_effect=RuntimeError("Concurrent DB save failure"))
    repo.mark_superseded = MagicMock(side_effect=RuntimeError("Concurrent DB supersession failure"))

    num_workers = 20
    ops_per_worker = 25
    total_executions = num_workers * ops_per_worker

    def worker_action(worker_id: int, op_id: int):
        if op_id % 2 == 0:
            manager.on_jsonl_record_written({
                "id": f"concurrent-rec-{worker_id}-{op_id}",
                "canonical_key": f"key-{worker_id}-{op_id}",
                "content": f"Content {worker_id}-{op_id}",
            })
        else:
            manager.on_jsonl_supersession_applied(
                f"pred-{worker_id}-{op_id}",
                f"succ-{worker_id}-{op_id}",
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(worker_action, w, o)
            for w in range(num_workers)
            for o in range(ops_per_worker)
        ]
        concurrent.futures.wait(futures)
        for f in futures:
            f.result()

    telemetry = manager.get_parity_telemetry()

    # Assert exact match with zero race conditions or dropped counts
    assert manager.dual_write_failure_count == total_executions
    assert manager.diverged_operations_count == total_executions
    assert telemetry["dual_write_failure_count"] == total_executions
    assert telemetry["diverged_operations_count"] == total_executions
    assert manager.parity_status == "diverged"
    assert telemetry["parity_status"] == "diverged"
    assert manager.last_failure_timestamp is not None
    assert manager.last_failure_reason is not None
    assert "Concurrent DB" in manager.last_failure_reason



"""Unit tests for PostgreSQL/SQLite Knowledge Model & Repository Abstraction."""

from datetime import datetime, timezone
import json
import os
import tempfile
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.knowledge.models import (
    Base,
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeRecord,
    VectorType,
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


def test_table_creation(db_session):
    """Verify tables knowledge_records, knowledge_evidence, and knowledge_aliases exist."""
    table_names = list(Base.metadata.tables.keys())
    assert "knowledge_records" in table_names
    assert "knowledge_evidence" in table_names
    assert "knowledge_aliases" in table_names


def test_crud_knowledge_record(repo, db_session):
    """Verify CRUD operations on KnowledgeRecord."""
    # Create
    record_data = {
        "id": "kcp-100",
        "canonical_key": "cancellation-fee",
        "content": "A fee of $50 applies for cancellations under 24 hours.",
        "normalised_content": "a fee of $50 applies for cancellations under 24 hours.",
        "instruction": "Explain cancellation rules politely.",
        "example_reply": "Our cancellation policy requires 24 hours notice or a $50 fee.",
        "account_key": "primary",
        "status": "active",
        "retrieval_enabled": True,
        "authority_level": "owner_instruction",
        "source_type": "manual_guidance",
        "confidence": 0.95,
        "revision": 1,
        "embedding": [0.1, 0.2, 0.3],
    }
    created = repo.save_record(record_data)
    assert created.id == "kcp-100"
    assert created.canonical_key == "cancellation-fee"
    assert created.account_key == "primary"
    assert created.retrieval_enabled is True
    assert created.confidence == 0.95
    assert created.embedding == [0.1, 0.2, 0.3]

    # Read
    fetched = repo.get_by_id("kcp-100")
    assert fetched is not None
    assert fetched.content == record_data["content"]
    assert fetched.instruction == "Explain cancellation rules politely."

    # Update
    update_data = {
        "id": "kcp-100",
        "content": "Updated fee is $75 for cancellations under 24 hours.",
        "revision": 2,
    }
    updated = repo.save_record(update_data)
    assert updated.content == "Updated fee is $75 for cancellations under 24 hours."
    assert updated.revision == 2

    # Verify to_dict serialization
    d = updated.to_dict()
    assert d["id"] == "kcp-100"
    assert d["revision"] == 2
    assert d["content"] == "Updated fee is $75 for cancellations under 24 hours."


def test_evidence_attachment_and_cascade_delete(repo, db_session):
    """Verify KnowledgeEvidence creation and cascade deletion on record delete."""
    record_data = {
        "id": "kcp-evidence-test",
        "canonical_key": "parking-info",
        "content": "Free customer parking is available in the back.",
        "account_key": "primary",
    }
    evidence_data = {
        "source_type": "sms_message",
        "source_message_id": "msg-12345",
        "source_thread_id": "thread-67890",
        "original_text_reference": "Customer asked: where do I park?",
    }
    record = repo.save_record(record_data, evidence_data=evidence_data)
    assert len(record.evidence_items) == 1
    ev = record.evidence_items[0]
    assert ev.source_type == "sms_message"
    assert ev.source_message_id == "msg-12345"
    assert ev.original_text_reference == "Customer asked: where do I park?"
    assert ev.to_dict()["source_thread_id"] == "thread-67890"

    # Delete parent record and verify cascade
    db_session.delete(record)
    db_session.commit()

    ev_lookup = db_session.get(KnowledgeEvidence, ev.id)
    assert ev_lookup is None


def test_alias_management(repo, db_session):
    """Verify add_alias and get_aliases with cascade delete."""
    rec = repo.save_record({
        "id": "kcp-alias-test",
        "canonical_key": "wifi-password",
        "content": "Guest wifi password is GuestPass2026.",
    })

    alias1 = repo.add_alias(rec.id, "What's the wifi code?", source="customer_variant")
    alias2 = repo.add_alias(rec.id, "Do you have internet access?", source="customer_variant")

    assert alias1.normalised_utterance == "what's the wifi code?"
    assert alias2.utterance == "Do you have internet access?"

    aliases = repo.get_aliases(rec.id)
    assert len(aliases) == 2
    assert {a.utterance for a in aliases} == {
        "What's the wifi code?",
        "Do you have internet access?",
    }

    # Empty utterance validation
    with pytest.raises(ValueError, match="Utterance cannot be empty"):
        repo.add_alias(rec.id, "   ")

    # Cascade delete verification
    db_session.delete(rec)
    db_session.commit()
    assert len(repo.get_aliases("kcp-alias-test")) == 0


def test_supersession_linking(repo):
    """Verify predecessor and successor linking via mark_superseded."""
    rec1 = repo.save_record({
        "id": "kcp-orig",
        "canonical_key": "refund-policy",
        "content": "No refunds after 7 days.",
        "status": "active",
        "retrieval_enabled": True,
    })

    rec2 = repo.save_record({
        "id": "kcp-revised",
        "canonical_key": "refund-policy",
        "content": "Full refunds within 14 days.",
        "status": "active",
        "retrieval_enabled": True,
        "revision": 2,
    })

    repo.mark_superseded(predecessor_id="kcp-orig", successor_id="kcp-revised")

    old_rec = repo.get_by_id("kcp-orig")
    new_rec = repo.get_by_id("kcp-revised")

    assert old_rec.status == "superseded"
    assert old_rec.superseded_by_id == "kcp-revised"
    assert new_rec.supersedes_id == "kcp-orig"


def test_list_active_scoping(repo):
    """Verify list_active filters by status, retrieval_enabled, and account scope."""
    repo.save_record({
        "id": "r1",
        "canonical_key": "k1",
        "content": "Line 1 active",
        "account_key": "primary",
        "status": "active",
        "retrieval_enabled": True,
    })
    repo.save_record({
        "id": "r2",
        "canonical_key": "k2",
        "content": "Line 2 active",
        "account_key": "secondary",
        "status": "active",
        "retrieval_enabled": True,
    })
    repo.save_record({
        "id": "r3",
        "canonical_key": "k3",
        "content": "Shared rule",
        "account_key": "shared",
        "status": "active",
        "retrieval_enabled": True,
    })
    repo.save_record({
        "id": "r4",
        "canonical_key": "k4",
        "content": "Disabled rule",
        "account_key": "primary",
        "status": "active",
        "retrieval_enabled": False,
    })
    repo.save_record({
        "id": "r5",
        "canonical_key": "k5",
        "content": "Quarantined rule",
        "account_key": "primary",
        "status": "quarantined",
        "retrieval_enabled": True,
    })

    # Primary with shared
    primary_active = repo.list_active("primary", include_shared=True)
    ids = {r.id for r in primary_active}
    assert ids == {"r1", "r3"}

    # Primary without shared
    primary_only = repo.list_active("primary", include_shared=False)
    assert {r.id for r in primary_only} == {"r1"}

    # Secondary with shared
    secondary_active = repo.list_active("secondary", include_shared=True)
    assert {r.id for r in secondary_active} == {"r2", "r3"}


def test_sync_from_jsonl_and_export_roundtrip(repo, tmp_path):
    """Verify JSONL import and round-trip export parity."""
    sample_records = [
        {
            "id": "jsonl-1",
            "canonical_key": "hours-operation",
            "content": "We are open Monday to Friday 9am - 5pm.",
            "sms_account_key": "primary",
            "status": "active",
            "retrieval_enabled": True,
            "authority_level": "canonical_knowledge",
            "revision": 1,
            "question": "What are your business hours?",
            "created_at": "2026-01-01T09:00:00+00:00",
        },
        {
            "id": "jsonl-2",
            "canonical_key": "emergency-contact",
            "content": "Call 000 for life-threatening emergencies.",
            "sms_account_key": "shared",
            "status": "active",
            "retrieval_enabled": True,
            "authority_level": "owner_instruction",
            "revision": 1,
            "created_at": "2026-01-02T10:00:00+00:00",
        },
    ]

    # Write initial JSONL
    source_jsonl = tmp_path / "test_learned.jsonl"
    with open(source_jsonl, "w", encoding="utf-8") as f:
        for item in sample_records:
            f.write(json.dumps(item) + "\n")

    # Sync into repository
    synced_count = repo.sync_from_jsonl(str(source_jsonl))
    assert synced_count == 2

    r1 = repo.get_by_id("jsonl-1")
    assert r1 is not None
    assert r1.canonical_key == "hours-operation"
    assert r1.account_key == "primary"
    assert len(r1.evidence_items) == 1
    assert r1.evidence_items[0].original_text_reference == "What are your business hours?"

    r2 = repo.get_by_id("jsonl-2")
    assert r2 is not None
    assert r2.account_key == "shared"

    # Export back to JSONL
    export_jsonl = tmp_path / "exported.jsonl"
    exported_count = repo.export_to_jsonl(str(export_jsonl))
    assert exported_count == 2

    # Verify exported content
    exported_lines = []
    with open(export_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                exported_lines.append(json.loads(line))

    assert len(exported_lines) == 2
    exported_by_id = {item["id"]: item for item in exported_lines}

    assert exported_by_id["jsonl-1"]["content"] == sample_records[0]["content"]
    assert exported_by_id["jsonl-1"]["canonical_key"] == "hours-operation"
    assert exported_by_id["jsonl-1"]["account_key"] == "primary"
    assert exported_by_id["jsonl-1"]["retrieval_enabled"] is True

    assert exported_by_id["jsonl-2"]["content"] == sample_records[1]["content"]
    assert exported_by_id["jsonl-2"]["account_key"] == "shared"

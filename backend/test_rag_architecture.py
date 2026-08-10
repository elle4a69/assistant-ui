import os
import json
import pytest
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from main import (
    SMSExampleIndex,
    DATASET_FILE,
    CANONICAL_INTENT_TAXONOMY,
    get_style_examples,
    classify_query_intent,
    app,
)
from fastapi.testclient import TestClient

client = TestClient(app)


def test_approved_intent_examples_dataset_exists_and_valid():
    assert DATASET_FILE.exists(), f"Dataset file {DATASET_FILE} should exist"
    index = SMSExampleIndex(DATASET_FILE)
    assert index.validation_status == "valid"
    assert index.dataset_hash != ""
    assert len(index.examples) > 0
    assert "availability" in index.intent_counts
    assert "booking_request" in index.intent_counts


def test_schema_validation_missing_file(tmp_path):
    missing_path = tmp_path / "non_existent.jsonl"
    with pytest.raises(FileNotFoundError):
        SMSExampleIndex(missing_path)


def test_schema_validation_non_approved_status(tmp_path):
    bad_file = tmp_path / "bad_status.jsonl"
    bad_file.write_text(
        json.dumps({
            "id": "ex_001",
            "intent": "availability",
            "review_status": "pending",
            "messages": [
                {"role": "user", "content": "Are you free today?"},
                {"role": "assistant", "content": "Yes I am."}
            ]
        }) + "\n",
        encoding="utf-8"
    )
    with pytest.raises(ValueError, match="review_status must be 'approved'"):
        SMSExampleIndex(bad_file)


def test_schema_validation_duplicate_id(tmp_path):
    dup_file = tmp_path / "dup_id.jsonl"
    rec1 = json.dumps({
        "id": "ex_same",
        "intent": "availability",
        "review_status": "approved",
        "messages": [
            {"role": "user", "content": "Are you free today?"},
            {"role": "assistant", "content": "Yes I am."}
        ]
    })
    rec2 = json.dumps({
        "id": "ex_same",
        "intent": "pricing",
        "review_status": "approved",
        "messages": [
            {"role": "user", "content": "How much for 1hr?"},
            {"role": "assistant", "content": "Rates are on my site."}
        ]
    })
    dup_file.write_text(f"{rec1}\n{rec2}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate ID"):
        SMSExampleIndex(dup_file)


def test_schema_validation_unknown_intent(tmp_path):
    unknown_intent_file = tmp_path / "unknown_intent.jsonl"
    unknown_intent_file.write_text(
        json.dumps({
            "id": "ex_002",
            "intent": "alien_telepathy_request",
            "review_status": "approved",
            "messages": [
                {"role": "user", "content": "Can you hear my thoughts?"},
                {"role": "assistant", "content": "No."}
            ]
        }) + "\n",
        encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not in canonical taxonomy"):
        SMSExampleIndex(unknown_intent_file)


def test_schema_validation_unresolved_placeholders(tmp_path):
    placeholder_file = tmp_path / "unmapped.jsonl"
    # Testing <UNMAPPED_...>, {booking_url}, and {unmapped_var}
    recs = [
        {"id": "p1", "intent": "booking_request", "review_status": "approved", "messages": [{"role": "user", "content": "Book me"}, {"role": "assistant", "content": "Use link <UNMAPPED_LINK>"}]},
        {"id": "p2", "intent": "booking_request", "review_status": "approved", "messages": [{"role": "user", "content": "Book me"}, {"role": "assistant", "content": "Use {booking_url}"}]},
        {"id": "p3", "intent": "booking_request", "review_status": "approved", "messages": [{"role": "user", "content": "Book me"}, {"role": "assistant", "content": "Hello {unmapped_name}"}]},
    ]
    for idx, rec in enumerate(recs):
        f_path = tmp_path / f"ph_{idx}.jsonl"
        f_path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Unresolved placeholder"):
            SMSExampleIndex(f_path)


def test_retrieval_minimum_relevance_threshold_and_zero_score():
    index = SMSExampleIndex(DATASET_FILE, min_score=0.5)
    # Query with zero matching terms
    assert index.search("quantum thermodynamics accelerator") == []


def test_retrieval_intent_boost():
    index = SMSExampleIndex(DATASET_FILE)
    results = index.search("book 1 hour incall", intent="booking_request", limit=3)
    assert len(results) > 0
    assert len(results) <= 3


def test_retrieval_limit_max_3():
    index = SMSExampleIndex(DATASET_FILE)
    results = index.search("book available free rates", limit=10)
    assert len(results) <= 3


def test_retrieval_deduplicate_replies(tmp_path):
    dedup_file = tmp_path / "dedup.jsonl"
    recs = [
        {"id": "d1", "intent": "pricing", "review_status": "approved", "messages": [{"role": "user", "content": "What are your rates?"}, {"role": "assistant", "content": "Rates are on my website."}]},
        {"id": "d2", "intent": "pricing", "review_status": "approved", "messages": [{"role": "user", "content": "How much for rates?"}, {"role": "assistant", "content": "Rates are on my website."}]},
    ]
    dedup_file.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    index = SMSExampleIndex(dedup_file, min_score=0.0)
    results = index.search("rates", limit=3)
    assert len(results) == 1


def test_retrieval_character_budget_enforcement(tmp_path):
    budget_file = tmp_path / "budget.jsonl"
    recs = [
        {"id": "b1", "intent": "availability", "review_status": "approved", "messages": [{"role": "user", "content": "Are you free today at 3pm?"}, {"role": "assistant", "content": "Yes I am available today at 3pm for a session."}]},
        {"id": "b2", "intent": "availability", "review_status": "approved", "messages": [{"role": "user", "content": "Are you open today at 4pm?"}, {"role": "assistant", "content": "Yes I am open today at 4pm for a session."}]},
    ]
    budget_file.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    # Setting max_budget_chars very small (e.g. 70 chars) allows only 1 pair
    index = SMSExampleIndex(budget_file, min_score=0.0, max_budget_chars=70)
    results = index.search("today 3pm 4pm", limit=3)
    assert len(results) == 1


def test_admin_rag_status_endpoint(monkeypatch):
    monkeypatch.setattr("main.STYLE_EXAMPLES_ENABLED", True)
    monkeypatch.setattr("main.example_index", SMSExampleIndex(DATASET_FILE))

    response = client.get("/api/admin/rag/status")
    assert response.status_code == 200
    data = response.json()
    assert data["validation_status"] == "valid"
    assert "dataset_hash" in data
    assert data["total_examples"] >= 20
    assert "intent_counts" in data
    assert "availability" in data["intent_counts"]

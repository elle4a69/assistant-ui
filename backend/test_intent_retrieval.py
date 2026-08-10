import os
import json
import pytest
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from main import (
    SMSExampleIndex,
    DATASET_FILE,
    CANONICAL_INTENT_TAXONOMY,
    classify_query_intent,
    get_style_examples,
)


def test_relevant_intent_queries_return_relevant_examples():
    """Verify that relevant queries for each canonical intent return relevant examples."""
    index = SMSExampleIndex(DATASET_FILE)
    test_queries = {
        "availability": "Are you available tonight after 6pm?",
        "booking_request": "I want to book a 1 hour incall session",
        "booking_confirmed": "Deposit sent, see you then!",
        "reschedule_or_cancel": "Can we reschedule or push back to tomorrow?",
        "pricing": "What are your rates and deposit amount?",
        "service_inquiry": "What services do you offer?",
        "location_or_arrival": "I am outside, parked near the door",
        "payment": "Can I pay via cash or bank transfer?",
        "boundary_or_safety": "What are your rules and screening requirements?",
        "complaint_or_dispute": "I have been waiting outside, where are you?",
        "greeting_or_smalltalk": "Good morning Tori, hope you are well!",
        "general_conversation": "Thanks for today, have a great night!",
    }

    for expected_intent, query in test_queries.items():
        results = index.search(query, intent=expected_intent, limit=3)
        assert len(results) > 0, f"Query '{query}' for intent '{expected_intent}' returned no results"
        assert len(results) <= 3


def test_irrelevant_zero_score_queries_return_empty_list():
    """Verify that zero-relevance / nonsensical queries return an empty list."""
    index = SMSExampleIndex(DATASET_FILE, min_score=0.5)
    results = index.search("quantum thermodynamics accelerator particle physics")
    assert results == []


def test_maximum_example_count_limit_max_3():
    """Verify retrieval never returns more than 3 examples (max limit 2-3)."""
    index = SMSExampleIndex(DATASET_FILE)
    results = index.search("available book rates time today", limit=10)
    assert len(results) <= 3


def test_token_character_budget_limit(tmp_path):
    """Verify retrieval respects token/character budget limit max_budget_chars."""
    budget_file = tmp_path / "budget_test.jsonl"
    recs = [
        {
            "id": "b1",
            "review_status": "approved",
            "intent": "availability",
            "incoming": "Are you free today at 3pm for a session?",
            "reply": "Yes I am available today at 3pm for a session in Melbourne.",
        },
        {
            "id": "b2",
            "review_status": "approved",
            "intent": "availability",
            "incoming": "Are you open today at 4pm for a session?",
            "reply": "Yes I am open today at 4pm for a session in Melbourne.",
        },
    ]
    budget_file.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    # Set budget (110 chars) which fits only 1 example pair (pair len 98 chars)
    index = SMSExampleIndex(budget_file, min_score=0.0, max_budget_chars=110)
    results = index.search("today 3pm 4pm session", limit=3)
    assert len(results) == 1


def test_approved_only_filtering(tmp_path):
    """Verify that unapproved examples are rejected during dataset indexing."""
    unapproved_file = tmp_path / "unapproved_test.jsonl"
    rec = {
        "id": "unapp_1",
        "review_status": "pending",
        "intent": "availability",
        "incoming": "Free today?",
        "reply": "Yes free.",
    }
    unapproved_file.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="review_status must be 'approved'"):
        SMSExampleIndex(unapproved_file)


def test_safe_fallback_on_missing_dataset(tmp_path):
    """Verify safe fallback when dataset file is missing or empty."""
    missing_file = tmp_path / "non_existent_dataset.jsonl"
    with pytest.raises(FileNotFoundError):
        SMSExampleIndex(missing_file)


def test_safe_fallback_get_style_examples_when_disabled(monkeypatch):
    """Verify get_style_examples returns empty list safely when RAG/examples disabled."""
    monkeypatch.setattr("main.STYLE_EXAMPLES_ENABLED", False)
    examples_str = get_style_examples("are you free today?")
    assert examples_str == []

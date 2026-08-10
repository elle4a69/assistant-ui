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


def test_australian_colloquialisms_classification():
    """Verify intent classification accurately handles Australian English colloquialisms."""
    test_cases = [
        ("can I book tonight", "booking_request"),
        ("are you free", "availability"),
        ("outside now", "location_or_arrival"),
        ("u free tonight?", "availability"),
        ("out front near the door", "location_or_arrival"),
        ("how much for 1hr", "pricing"),
        ("what are your rates for incall", "pricing"),
        ("gday tori, hope you are well", "greeting_or_smalltalk"),
        ("deposit paid see u then", "booking_confirmed"),
        ("cheers ta for today", "general_conversation"),
        ("do you do raw or no condom", "boundary_or_safety"),
        ("been waiting outside 20 mins where r u", "complaint_or_dispute"),
        ("can we push back to 6pm", "reschedule_or_cancel"),
        ("do you take payid or cash", "payment"),
        ("where are you based in sydney", "service_inquiry"),
    ]
    for query, expected_intent in test_cases:
        actual_intent = classify_query_intent(query)
        assert actual_intent == expected_intent, f"Query '{query}' expected '{expected_intent}', got '{actual_intent}'"


def test_multi_intent_priority():
    """Verify multi-intent messages prioritize actionable primary intent correctly."""
    multi_intent_cases = [
        ("Hey Tori! How are you? Can I book a 1 hour session tonight?", "booking_request"),
        ("Hi babe! How much are your rates for incall?", "pricing"),
        ("Good morning! Are you free this afternoon around 3?", "availability"),
        ("I'm outside now, which doorbell do I ring?", "location_or_arrival"),
        ("I've been waiting 20 minutes outside! Need to cancel.", "complaint_or_dispute"),
    ]
    for query, expected_intent in multi_intent_cases:
        actual_intent = classify_query_intent(query)
        assert actual_intent == expected_intent, f"Multi-intent query '{query}' expected '{expected_intent}', got '{actual_intent}'"


def test_strict_intent_filtering_no_cross_intent():
    """Verify SMSExampleIndex search never returns cross-intent examples."""
    index = SMSExampleIndex(DATASET_FILE)
    for intent in CANONICAL_INTENT_TAXONOMY:
        results = index.search("available book rates time address payid", intent=intent, limit=3)
        for user_text, reply_text in results:
            matching = [ex for ex in index.examples if ex["user_text"] == user_text and ex["reply_text"] == reply_text]
            assert len(matching) > 0
            for match_ex in matching:
                assert match_ex["intent"] == intent, f"Returned example with intent '{match_ex['intent']}' when target intent was '{intent}'"


def test_minimum_score_threshold_enforcement():
    """Verify BM25 minimum score threshold (0.5) filters low-relevance results."""
    index = SMSExampleIndex(DATASET_FILE, min_score=0.5)
    results = index.search("quantum mechanics astrophysics relativity", intent="pricing")
    assert results == []


def test_strict_prompt_budget_skipping_oversized_first_example(tmp_path):
    """Verify oversized first example (> 500 chars) is skipped instead of bypassing prompt budget."""
    dataset_file = tmp_path / "oversized_test.jsonl"
    recs = [
        {
            "id": "over_1",
            "review_status": "approved",
            "intent": "availability",
            "incoming": "Are you free today for a long appointment session in Melbourne CBD?",
            "reply": "A" * 520,  # 520 chars, pair_len = 520 + 68 = 588 chars > 500
        },
        {
            "id": "valid_2",
            "review_status": "approved",
            "intent": "availability",
            "incoming": "Are you free today at 3pm?",
            "reply": "Yes, I am available at 3pm today in Melbourne CBD.",
        },
    ]
    dataset_file.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    index = SMSExampleIndex(dataset_file, min_score=0.0, max_budget_chars=500)
    results = index.search("free today 3pm melbourne cbd", intent="availability", limit=3)

    assert len(results) == 1
    assert results[0][0] == "Are you free today at 3pm?"
    assert len(results[0][0]) + len(results[0][1]) <= 500


def test_reply_deduplication(tmp_path):
    """Verify near-identical replies are deduplicated in retrieval output."""
    dataset_file = tmp_path / "dedup_test.jsonl"
    recs = [
        {
            "id": "d1",
            "review_status": "approved",
            "intent": "pricing",
            "incoming": "What are your rates for 1 hour?",
            "reply": "My rate for 1 hour is $300 incall.",
        },
        {
            "id": "d2",
            "review_status": "approved",
            "intent": "pricing",
            "incoming": "How much for 1hr incall session?",
            "reply": "My rate for 1 hour is $300 incall!",  # Near identical punctuation/case
        },
    ]
    dataset_file.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    index = SMSExampleIndex(dataset_file, min_score=0.0)
    results = index.search("rates 1 hour incall session", intent="pricing", limit=3)

    assert len(results) == 1

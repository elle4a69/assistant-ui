"""Unit tests for Phase 7: Observability, Decision Tracing & Feedback Signals.

Covers:
- Decision trace captures all required fields answering the 8 core questions.
- Privacy First: raw customer phone numbers, emails, and card numbers are never stored in traces.
- DecisionTraceStore capacity bounding (FIFO eviction) and thread-safe concurrent access.
- classify_human_correction:
  - Purely stylistic/greeting changes classified as style_only / operator_preference
    with is_knowledge_candidate=False.
  - Substantive business rule / policy additions classified as missing_knowledge
    with is_knowledge_candidate=True.
  - Altering prices/times/dates/URLs classified as dynamic_data_failure
    with is_knowledge_candidate=False.
  - Contradiction of active knowledge classified as incorrect_knowledge
    with is_knowledge_candidate=True.
  - Retrieval failure detected when candidate pool contained the fact
    with is_knowledge_candidate=False.
- Outcome signals tracking, filtering, aggregation, and capacity bounding.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest

from backend.observability import (
    DecisionTraceStore,
    EditClassification,
    EditClassificationResult,
    OutcomeSignal,
    OutcomeSignalRecord,
    OutcomeSignalTracker,
    ResponderDecisionTrace,
    classify_human_correction,
    hash_sensitive_value,
    redact_sensitive_text,
    sanitize_preview,
)


# ===========================================================================
# 1. Privacy Sanitization Tests
# ===========================================================================

def test_redact_sensitive_text_redacts_phones_emails_cards():
    """Verify raw phone numbers, emails, and credit cards are redacted."""
    raw = (
        "Customer says: Call me on 0412 345 678 or +61498765432. "
        "Email is test.user@example.com and card is 4111 2222 3333 4444."
    )
    redacted = redact_sensitive_text(raw)
    assert "0412 345 678" not in redacted
    assert "+61498765432" not in redacted
    assert "test.user@example.com" not in redacted
    assert "4111 2222 3333 4444" not in redacted
    assert "[PHONE_REDACTED]" in redacted
    assert "[EMAIL_REDACTED]" in redacted
    assert "[CARD_REDACTED]" in redacted


def test_sanitize_preview_bounds_length_and_redacts():
    """Verify preview is truncated and redacted."""
    raw = "My phone is 0412 345 678. " + ("word " * 50)
    preview = sanitize_preview(raw, max_chars=60)
    assert len(preview) <= 65
    assert "0412 345 678" not in preview
    assert "[PHONE_REDACTED]" in preview
    assert preview.endswith("...")


def test_hash_sensitive_value_is_deterministic():
    """Verify hashing produces consistent, non-empty sha256."""
    h1 = hash_sensitive_value("+61412345678")
    h2 = hash_sensitive_value("+61412345678")
    h3 = hash_sensitive_value("+61499999999")
    assert len(h1) == 64
    assert h1 == h2
    assert h1 != h3


# ===========================================================================
# 2. ResponderDecisionTrace Completeness & Privacy Tests
# ===========================================================================

def test_decision_trace_answers_all_eight_core_questions():
    """Verify ResponderDecisionTrace captures all 8 required audit questions."""
    trace = ResponderDecisionTrace.create(
        customer_query="What time are you open tomorrow?",
        detected_intent="availability",
        intent_confidence=0.96,
        dynamic_systems_queried=["booking_calendar", "business_hours"],
        dynamic_data_context={"today": "2026-09-16", "available_slots": ["10:00am", "2:00pm"]},
        retrieved_candidates=[
            {"id": "rec-1", "canonical_key": "hours", "authority_level": "owner_instruction", "score": 0.92},
            {"id": "rec-2", "canonical_key": "hours", "authority_level": "canonical_knowledge", "score": 0.85},
        ],
        rejected_candidates=[
            {"id": "rec-2", "reason": "authority_subordinate"},
        ],
        winning_record_id="rec-1",
        winning_canonical_key="hours",
        winning_authority_level="owner_instruction",
        winning_reason="highest_authority:owner_instruction",
        had_conflict=False,
        conflict_details=None,
        style_examples_used=[{"id": "style-01", "intent": "availability"}],
        final_response="We are open tomorrow from 9:00am to 5:00pm.",
        tenant_id="tenant-alpha",
        account_key="primary",
        thread_id="thread-xyz",
        customer_phone_or_id="+61412345678",
    )

    # Question 1: What did the customer ask (privacy-safe preview/hash)?
    assert trace.customer_query_hash == hash_sensitive_value("What time are you open tomorrow?")
    assert "What time are you open tomorrow?" in trace.customer_query_preview

    # Question 2: What intent was detected?
    assert trace.detected_intent == "availability"
    assert trace.intent_confidence == 0.96

    # Question 3: What dynamic systems were queried?
    assert trace.dynamic_systems_queried == ["booking_calendar", "business_hours"]
    assert trace.dynamic_data_context["today"] == "2026-09-16"

    # Question 4: What knowledge candidates were retrieved / rejected?
    assert len(trace.retrieved_candidates) == 2
    assert len(trace.rejected_candidates) == 1
    assert trace.rejected_candidates[0]["id"] == "rec-2"

    # Question 5: Which record won, why, and what authority did it have?
    assert trace.winning_record_id == "rec-1"
    assert trace.winning_canonical_key == "hours"
    assert trace.winning_authority_level == "owner_instruction"
    assert trace.winning_reason == "highest_authority:owner_instruction"

    # Question 6: Was there a conflict?
    assert trace.had_conflict is False
    assert trace.conflict_details is None

    # Question 7: What style examples were used?
    assert len(trace.style_examples_used) == 1
    assert trace.style_examples_used[0]["id"] == "style-01"

    # Question 8: What was the final generated response?
    assert trace.final_response == "We are open tomorrow from 9:00am to 5:00pm."

    # Serialization test
    d = trace.to_dict()
    assert d["trace_id"] == trace.trace_id
    assert d["account_key"] == "primary"
    assert d["tenant_id"] == "tenant-alpha"


def test_decision_trace_privacy_redacts_raw_phone_number_in_query_and_response():
    """Verify raw customer phone numbers are NEVER stored unredacted in a trace."""
    trace = ResponderDecisionTrace.create(
        customer_query="Hey, call my mobile 0412345678 when ready!",
        customer_phone_or_id="0412345678",
        thread_id="0412345678",
        final_response="Sure, we will send an SMS to 0412345678 shortly.",
    )

    # Query preview must be redacted
    assert "0412345678" not in trace.customer_query_preview
    assert "[PHONE_REDACTED]" in trace.customer_query_preview

    # Final response must be redacted
    assert "0412345678" not in trace.final_response
    assert "[PHONE_REDACTED]" in trace.final_response

    # Customer identifier is a hash, not raw phone
    assert "0412345678" not in (trace.customer_hash or "")
    assert trace.customer_hash == hash_sensitive_value("0412345678")

    # Thread id should not be a raw phone number
    assert trace.thread_id is not None
    assert "0412345678" not in trace.thread_id


# ===========================================================================
# 3. DecisionTraceStore Bounding & Thread-Safety Tests
# ===========================================================================

def test_decision_trace_store_capacity_bounding():
    """Verify store enforces maximum capacity via FIFO eviction."""
    store = DecisionTraceStore(capacity=3)
    assert store.capacity == 3

    t1 = ResponderDecisionTrace(trace_id="trace-1")
    t2 = ResponderDecisionTrace(trace_id="trace-2")
    t3 = ResponderDecisionTrace(trace_id="trace-3")
    t4 = ResponderDecisionTrace(trace_id="trace-4")

    store.record_trace(t1)
    store.record_trace(t2)
    store.record_trace(t3)
    assert store.count() == 3

    # Adding 4th should evict trace-1
    store.record_trace(t4)
    assert store.count() == 3
    assert store.get_trace("trace-1") is None
    assert store.get_trace("trace-2") is not None
    assert store.get_trace("trace-3") is not None
    assert store.get_trace("trace-4") is not None


def test_decision_trace_store_query_methods():
    """Verify list_recent_traces and get_traces_for_thread."""
    store = DecisionTraceStore(capacity=10)

    t1 = ResponderDecisionTrace(trace_id="t1", account_key="primary", thread_id="th-A")
    t2 = ResponderDecisionTrace(trace_id="t2", account_key="secondary", thread_id="th-B")
    t3 = ResponderDecisionTrace(trace_id="t3", account_key="primary", thread_id="th-A")

    store.record_trace(t1)
    store.record_trace(t2)
    store.record_trace(t3)

    recent_primary = store.list_recent_traces(limit=10, account_key="primary")
    assert len(recent_primary) == 2
    assert [t.trace_id for t in recent_primary] == ["t3", "t1"]

    thread_traces = store.get_traces_for_thread("th-A")
    assert len(thread_traces) == 2
    assert [t.trace_id for t in thread_traces] == ["t3", "t1"]


def test_decision_trace_store_concurrent_thread_safety():
    """Verify concurrent reads and writes do not corrupt state or exceed capacity."""
    store = DecisionTraceStore(capacity=20)

    def worker(i: int):
        t = ResponderDecisionTrace(trace_id=f"worker-{i}", thread_id=f"thread-{i % 5}")
        store.record_trace(t)
        store.get_trace(f"worker-{i}")
        store.list_recent_traces(limit=5)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(worker, range(100)))

    assert store.count() <= 20


# ===========================================================================
# 4. Correction Classifier Tests
# ===========================================================================

def test_classify_stylistic_and_greeting_changes_produces_no_knowledge():
    """Verify purely stylistic/greeting/emoji changes are style_only and NEVER produce knowledge."""
    draft = "Hello, our studio is open from 9am to 5pm."
    edited = "Hi there! Our studio is open from 9am to 5pm. Cheers! 😊"

    result = classify_human_correction(draft, edited)
    assert result.classification in (EditClassification.STYLE_ONLY, EditClassification.OPERATOR_PREFERENCE)
    assert result.is_knowledge_candidate is False
    assert result.confidence >= 0.80


def test_classify_tone_and_politeness_only():
    """Verify polite filler additions do not trigger business knowledge extraction."""
    draft = "We do not allow pets inside."
    edited = "Hi! Unfortunately, we cannot allow pets inside the studio. Thanks for understanding!"

    result = classify_human_correction(draft, edited)
    assert result.is_knowledge_candidate is False
    assert result.classification in (EditClassification.STYLE_ONLY, EditClassification.OPERATOR_PREFERENCE)


def test_classify_identical_draft_and_edit():
    """Verify identical text classifies cleanly as style_only with no knowledge candidate."""
    text = "Our studio address is 123 Main Street."
    result = classify_human_correction(text, text)
    assert result.classification == EditClassification.STYLE_ONLY
    assert result.is_knowledge_candidate is False
    assert result.confidence == 1.0


def test_classify_missing_knowledge_business_rule_addition():
    """Verify adding a missing business rule is classified as missing_knowledge and enters pipeline."""
    draft = "Yes, we are open tomorrow from 9am."
    edited = (
        "Yes, we are open tomorrow from 9am. "
        "Please note that parking is strictly available at the rear and visitors must bring photo ID."
    )

    result = classify_human_correction(draft, edited)
    assert result.classification == EditClassification.MISSING_KNOWLEDGE
    assert result.is_knowledge_candidate is True
    assert result.extracted_knowledge_snippet is not None
    assert "parking" in result.extracted_knowledge_snippet.lower() or "id" in result.extracted_knowledge_snippet.lower()


def test_classify_cancellation_policy_addition():
    """Verify adding a cancellation policy is classified as missing_knowledge."""
    draft = "Your booking is received."
    edited = (
        "Your booking is received. "
        "We require 24 hours notice for any cancellations, otherwise deposits are non-refundable."
    )

    result = classify_human_correction(draft, edited)
    assert result.classification == EditClassification.MISSING_KNOWLEDGE
    assert result.is_knowledge_candidate is True
    assert "cancellations" in result.extracted_knowledge_snippet.lower() or "non-refundable" in result.extracted_knowledge_snippet.lower()


def test_classify_altering_prices_as_dynamic_data_failure():
    """Verify modifying a price is classified as dynamic_data_failure and NOT knowledge candidate."""
    draft = "The 60-minute massage is $120."
    edited = "The 60-minute massage is $150."

    result = classify_human_correction(draft, edited)
    assert result.classification == EditClassification.DYNAMIC_DATA_FAILURE
    assert result.is_knowledge_candidate is False
    assert result.detected_dynamic_elements is not None


def test_classify_altering_dates_and_times_as_dynamic_data_failure():
    """Verify altering date or time slots is dynamic_data_failure."""
    draft = "We have an open appointment today at 2:00pm."
    edited = "We have an open appointment tomorrow at 4:30pm."

    result = classify_human_correction(draft, edited)
    assert result.classification == EditClassification.DYNAMIC_DATA_FAILURE
    assert result.is_knowledge_candidate is False


def test_classify_altering_booking_urls_as_dynamic_data_failure():
    """Verify altering dynamic booking links is dynamic_data_failure."""
    draft = "You can view availability here: https://oldbooking.com/sched"
    edited = "You can view availability here: https://newbooking.com/sched"

    result = classify_human_correction(draft, edited)
    assert result.classification == EditClassification.DYNAMIC_DATA_FAILURE
    assert result.is_knowledge_candidate is False


def test_classify_incorrect_knowledge_with_trace_context():
    """Verify that correcting a retrieved fact is classified as incorrect_knowledge."""
    trace = ResponderDecisionTrace(
        trace_id="tr-100",
        winning_record_id="rec-pets",
        winning_canonical_key="pet_policy",
        final_response="Pets are strictly prohibited on the premises.",
    )
    draft = "Pets are strictly prohibited on the premises."
    edited = "Small dogs under 10kg are allowed inside if kept in a carrier."

    result = classify_human_correction(draft, edited, trace=trace)
    assert result.classification in (EditClassification.INCORRECT_KNOWLEDGE, EditClassification.MISSING_KNOWLEDGE)
    assert result.is_knowledge_candidate is True


def test_classify_retrieval_failure_when_candidates_contained_fact():
    """Verify that when candidate pool had the fact but retrieval didn't select it, it's retrieval_failure."""
    trace = ResponderDecisionTrace(
        trace_id="tr-101",
        retrieved_candidates=[
            {"id": "cand-1", "canonical_key": "parking", "text": "Free customer parking is in the underground garage."},
        ],
        winning_record_id=None,
        had_conflict=True,
    )
    draft = "I am not sure about parking availability."
    edited = "Free customer parking is available in the underground garage."

    result = classify_human_correction(draft, edited, trace=trace)
    assert result.classification == EditClassification.RETRIEVAL_FAILURE
    assert result.is_knowledge_candidate is False


def test_classify_wrong_scope_correction():
    """Verify changing line identity from primary to secondary is classified as wrong_scope."""
    trace = ResponderDecisionTrace(
        trace_id="tr-102",
        account_key="primary",
    )
    draft = "Welcome to the primary line."
    edited = "Welcome to the secondary line."

    result = classify_human_correction(draft, edited, trace=trace)
    assert result.classification == EditClassification.WRONG_SCOPE
    assert result.is_knowledge_candidate is False


# ===========================================================================
# 5. Outcome Signals Tracking Tests
# ===========================================================================

def test_outcome_signals_tracking_and_retrieval():
    """Verify OutcomeSignalTracker records, retrieves, and filters signals."""
    tracker = OutcomeSignalTracker(capacity=100)

    r1 = tracker.record_signal(
        signal=OutcomeSignal.RESPONSE_SENT_WITHOUT_EDIT,
        trace_id="trace-001",
        thread_id="thread-001",
        tenant_id="tenant-1",
        account_key="primary",
        customer_phone="+61412345678",
    )

    r2 = tracker.record_signal(
        signal=OutcomeSignal.RESPONSE_EDITED,
        trace_id="trace-002",
        thread_id="thread-002",
        tenant_id="tenant-1",
        account_key="primary",
    )

    r3 = tracker.record_signal(
        signal=OutcomeSignal.BOOKING_SUCCESS,
        trace_id="trace-001",
        thread_id="thread-001",
        tenant_id="tenant-2",
    )

    # Privacy check: customer_hash is hashed, not raw phone
    assert r1.customer_hash is not None
    assert "+61412345678" not in r1.customer_hash
    assert r1.customer_hash == hash_sensitive_value("+61412345678")

    # Retrieval
    assert tracker.get_signal(r1.signal_id) == r1
    assert tracker.count() == 3

    # Filtering by signal type
    sent = tracker.list_signals(signal=OutcomeSignal.RESPONSE_SENT_WITHOUT_EDIT)
    assert len(sent) == 1
    assert sent[0].signal_id == r1.signal_id

    # Filtering by trace_id
    t1_signals = tracker.list_signals(trace_id="trace-001")
    assert len(t1_signals) == 2

    # Aggregate counts
    counts = tracker.count_by_signal()
    assert counts[OutcomeSignal.RESPONSE_SENT_WITHOUT_EDIT.value] == 1
    assert counts[OutcomeSignal.RESPONSE_EDITED.value] == 1
    assert counts[OutcomeSignal.BOOKING_SUCCESS.value] == 1

    # Tenant-scoped counts
    tenant1_counts = tracker.count_by_signal(tenant_id="tenant-1")
    assert tenant1_counts[OutcomeSignal.RESPONSE_SENT_WITHOUT_EDIT.value] == 1
    assert tenant1_counts.get(OutcomeSignal.BOOKING_SUCCESS.value, 0) == 0


def test_outcome_signals_capacity_bounding():
    """Verify OutcomeSignalTracker bounds capacity and evicts oldest."""
    tracker = OutcomeSignalTracker(capacity=2)

    s1 = tracker.record_signal(OutcomeSignal.KNOWLEDGE_GAP_CREATED)
    s2 = tracker.record_signal(OutcomeSignal.HUMAN_TAKEOVER)
    assert tracker.count() == 2

    s3 = tracker.record_signal(OutcomeSignal.KNOWLEDGE_GAP_RESOLVED)
    assert tracker.count() == 2
    assert tracker.get_signal(s1.signal_id) is None
    assert tracker.get_signal(s2.signal_id) is not None
    assert tracker.get_signal(s3.signal_id) is not None

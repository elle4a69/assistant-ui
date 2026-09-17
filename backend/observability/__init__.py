"""Observability, Decision Tracing & Feedback Signals Package (Phase 7).

Exports:
- ResponderDecisionTrace: Structured audit trail for responder execution.
- DecisionTraceStore: Thread-safe, bounded in-memory store for traces.
- EditClassification: Enumeration of human correction classifications.
- EditClassificationResult: Detailed result of staff edit classification.
- classify_human_correction: Function classifying human edits and guarding business knowledge.
- OutcomeSignal: Enumeration of business and operational outcome signals.
- OutcomeSignalRecord: Correlated record for an outcome signal.
- OutcomeSignalTracker: Thread-safe, bounded tracker for outcome signals.
"""

from .decision_trace import (
    DecisionTraceStore,
    ResponderDecisionTrace,
    hash_sensitive_value,
    redact_sensitive_text,
    sanitize_preview,
)
from .correction_classifier import (
    EditClassification,
    EditClassificationResult,
    classify_human_correction,
)
from .outcome_signals import (
    OutcomeSignal,
    OutcomeSignalRecord,
    OutcomeSignalTracker,
)

__all__ = [
    "ResponderDecisionTrace",
    "DecisionTraceStore",
    "hash_sensitive_value",
    "redact_sensitive_text",
    "sanitize_preview",
    "EditClassification",
    "EditClassificationResult",
    "classify_human_correction",
    "OutcomeSignal",
    "OutcomeSignalRecord",
    "OutcomeSignalTracker",
]

"""Decision Tracing for Responder Execution (Phase 7).

Governing Rules:
1. Every responder execution produces a structured decision trace answering:
   - What did the customer ask (privacy-safe preview/hash)?
   - What intent was detected?
   - What dynamic systems were queried?
   - What knowledge candidates were retrieved / rejected?
   - Which record won, why, and what authority did it have?
   - Was there a conflict?
   - What style examples were used?
   - What was the final generated response?
2. Privacy First: Do NOT store raw phone numbers or sensitive unredacted SMS
   bodies in observability traces. Use hashes/IDs/sanitized previews.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
import threading
from typing import Any, Dict, List, Optional
import uuid

PHONE_PATTERN = re.compile(
    r"(?:\+?61|0)[2-478](?:[-.\s]?\d){8}\b"
    r"|\b(?:\+\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"
    r"|\b04\d{2}[-.\s]?\d{3}[-.\s]?\d{3}\b"
    r"|\b\d{10,12}\b",
    re.IGNORECASE,
)

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    re.IGNORECASE,
)

CARD_PATTERN = re.compile(
    r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
)


def redact_sensitive_text(text: str) -> str:
    """Redact phone numbers, emails, and card numbers from text."""
    if not text:
        return ""
    sanitized = CARD_PATTERN.sub("[CARD_REDACTED]", text)
    sanitized = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", sanitized)
    sanitized = PHONE_PATTERN.sub("[PHONE_REDACTED]", sanitized)
    return sanitized


def sanitize_preview(text: str, max_chars: int = 120) -> str:
    """Create a privacy-safe truncated preview of text with sensitive data redacted."""
    if not text:
        return ""
    redacted = redact_sensitive_text(str(text))
    # Collapse multiple whitespace
    collapsed = re.sub(r"\s+", " ", redacted).strip()
    if len(collapsed) > max_chars:
        return collapsed[:max_chars].rstrip() + "..."
    return collapsed


def hash_sensitive_value(value: str) -> str:
    """Produce deterministic SHA-256 hash for a sensitive string."""
    if not value:
        return ""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


@dataclass
class ResponderDecisionTrace:
    """Structured decision trace for a single responder execution.
    
    Adheres strictly to privacy-first rules:
    - Never stores raw phone numbers or raw sensitive customer SMS bodies.
    - Captures the complete reasoning lineage: query, intent, dynamic queries,
      candidate pool, winner resolution, conflicts, and style examples.
    """

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tenant_id: str = "default"
    account_key: str = "primary"
    thread_id: Optional[str] = None
    customer_hash: Optional[str] = None

    # 1. What did the customer ask? (privacy-safe preview & hash)
    customer_query_hash: str = ""
    customer_query_preview: str = ""

    # 2. What intent was detected?
    detected_intent: Optional[str] = None
    intent_confidence: Optional[float] = None

    # 3. What dynamic systems were queried?
    dynamic_systems_queried: List[str] = field(default_factory=list)
    dynamic_data_context: Optional[Dict[str, Any]] = None

    # 4. What knowledge candidates were retrieved / rejected?
    retrieved_candidates: List[Dict[str, Any]] = field(default_factory=list)
    rejected_candidates: List[Dict[str, Any]] = field(default_factory=list)

    # 5. Which record won, why, and what authority did it have?
    winning_record_id: Optional[str] = None
    winning_canonical_key: Optional[str] = None
    winning_authority_level: Optional[str] = None
    winning_reason: Optional[str] = None

    # 6. Was there a conflict?
    had_conflict: bool = False
    conflict_details: Optional[Dict[str, Any]] = None

    # 7. What style examples were used?
    style_examples_used: List[Dict[str, Any]] = field(default_factory=list)

    # 8. What was the final generated response?
    final_response: str = ""

    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Sanitize all text fields to guarantee privacy preservation."""
        if self.customer_query_preview:
            self.customer_query_preview = sanitize_preview(self.customer_query_preview)
        if self.final_response:
            self.final_response = redact_sensitive_text(self.final_response)
        if self.customer_hash and PHONE_PATTERN.search(self.customer_hash):
            self.customer_hash = hash_sensitive_value(self.customer_hash)
        if self.thread_id and PHONE_PATTERN.search(self.thread_id):
            self.thread_id = hash_sensitive_value(self.thread_id)

    @classmethod
    def create(
        cls,
        customer_query: str,
        detected_intent: Optional[str] = None,
        intent_confidence: Optional[float] = None,
        dynamic_systems_queried: Optional[List[str]] = None,
        dynamic_data_context: Optional[Dict[str, Any]] = None,
        retrieved_candidates: Optional[List[Dict[str, Any]]] = None,
        rejected_candidates: Optional[List[Dict[str, Any]]] = None,
        winning_record_id: Optional[str] = None,
        winning_canonical_key: Optional[str] = None,
        winning_authority_level: Optional[str] = None,
        winning_reason: Optional[str] = None,
        had_conflict: bool = False,
        conflict_details: Optional[Dict[str, Any]] = None,
        style_examples_used: Optional[List[Dict[str, Any]]] = None,
        final_response: str = "",
        tenant_id: str = "default",
        account_key: str = "primary",
        thread_id: Optional[str] = None,
        customer_phone_or_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ResponderDecisionTrace:
        """Factory method to construct a trace safely from raw runtime context."""
        query_hash = hash_sensitive_value(customer_query) if customer_query else ""
        query_preview = sanitize_preview(customer_query) if customer_query else ""
        cust_hash = hash_sensitive_value(customer_phone_or_id) if customer_phone_or_id else None

        # Sanitize thread_id if raw phone was passed as thread_id
        safe_thread_id = thread_id
        if safe_thread_id and PHONE_PATTERN.search(safe_thread_id):
            safe_thread_id = f"thread-{hash_sensitive_value(safe_thread_id)[:12]}"

        return cls(
            tenant_id=tenant_id,
            account_key=account_key,
            thread_id=safe_thread_id,
            customer_hash=cust_hash,
            customer_query_hash=query_hash,
            customer_query_preview=query_preview,
            detected_intent=detected_intent,
            intent_confidence=intent_confidence,
            dynamic_systems_queried=list(dynamic_systems_queried or []),
            dynamic_data_context=dynamic_data_context,
            retrieved_candidates=list(retrieved_candidates or []),
            rejected_candidates=list(rejected_candidates or []),
            winning_record_id=winning_record_id,
            winning_canonical_key=winning_canonical_key,
            winning_authority_level=winning_authority_level,
            winning_reason=winning_reason,
            had_conflict=had_conflict,
            conflict_details=conflict_details,
            style_examples_used=list(style_examples_used or []),
            final_response=final_response,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert trace to dictionary for serialisation or inspection."""
        return {
            "trace_id": self.trace_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "tenant_id": self.tenant_id,
            "account_key": self.account_key,
            "thread_id": self.thread_id,
            "customer_hash": self.customer_hash,
            "customer_query_hash": self.customer_query_hash,
            "customer_query_preview": self.customer_query_preview,
            "detected_intent": self.detected_intent,
            "intent_confidence": self.intent_confidence,
            "dynamic_systems_queried": self.dynamic_systems_queried,
            "dynamic_data_context": self.dynamic_data_context,
            "retrieved_candidates": self.retrieved_candidates,
            "rejected_candidates": self.rejected_candidates,
            "winning_record_id": self.winning_record_id,
            "winning_canonical_key": self.winning_canonical_key,
            "winning_authority_level": self.winning_authority_level,
            "winning_reason": self.winning_reason,
            "had_conflict": self.had_conflict,
            "conflict_details": self.conflict_details,
            "style_examples_used": self.style_examples_used,
            "final_response": self.final_response,
            "metadata": self.metadata,
        }


class DecisionTraceStore:
    """Thread-safe, capacity-bounded in-memory store for responder decision traces.
    
    Guarantees FIFO eviction when capacity is reached to prevent memory leaks.
    """

    def __init__(self, capacity: int = 1000) -> None:
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self._capacity = capacity
        self._traces: OrderedDict[str, ResponderDecisionTrace] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def record_trace(self, trace: ResponderDecisionTrace) -> None:
        """Store a decision trace in memory, evicting oldest if capacity reached."""
        with self._lock:
            if trace.trace_id in self._traces:
                self._traces.move_to_end(trace.trace_id)
                self._traces[trace.trace_id] = trace
                return

            if len(self._traces) >= self._capacity:
                self._traces.popitem(last=False)

            self._traces[trace.trace_id] = trace

    def get_trace(self, trace_id: str) -> Optional[ResponderDecisionTrace]:
        """Retrieve a specific trace by trace_id."""
        with self._lock:
            return self._traces.get(trace_id)

    def list_recent_traces(
        self,
        limit: int = 50,
        account_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[ResponderDecisionTrace]:
        """List most recent traces, ordered from newest to oldest."""
        with self._lock:
            results: List[ResponderDecisionTrace] = []
            for trace in reversed(self._traces.values()):
                if account_key and trace.account_key != account_key:
                    continue
                if tenant_id and trace.tenant_id != tenant_id:
                    continue
                results.append(trace)
                if len(results) >= limit:
                    break
            return results

    def get_traces_for_thread(
        self,
        thread_id: str,
        limit: int = 50,
    ) -> List[ResponderDecisionTrace]:
        """Retrieve traces associated with a specific conversation thread."""
        with self._lock:
            results: List[ResponderDecisionTrace] = []
            for trace in reversed(self._traces.values()):
                if trace.thread_id == thread_id:
                    results.append(trace)
                    if len(results) >= limit:
                        break
            return results

    def count(self) -> int:
        """Get the current count of traces stored."""
        with self._lock:
            return len(self._traces)

    def clear(self) -> None:
        """Clear all stored traces."""
        with self._lock:
            self._traces.clear()

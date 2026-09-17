"""Outcome Signals Tracking for Responder & Knowledge Lifecycle (Phase 7).

Governing Rules:
1. Track critical outcome signals:
   - response_sent_without_edit
   - response_edited
   - response_rejected
   - customer_repeated_question
   - human_takeover
   - booking_success
   - booking_failure
   - knowledge_gap_created
   - knowledge_gap_resolved
2. Provide thread-safe, bounded in-memory tracking with correlation metadata
   linking signals back to responder decision traces, threads, and tenants.
3. Privacy First: Ensure customer identifiers are hashed and unredacted PII is never stored.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import threading
from typing import Any, Dict, List, Optional, Union
import uuid

from .decision_trace import PHONE_PATTERN, hash_sensitive_value


class OutcomeSignal(str, Enum):
    """Signals representing operational and business outcomes."""

    RESPONSE_SENT_WITHOUT_EDIT = "response_sent_without_edit"
    RESPONSE_EDITED = "response_edited"
    RESPONSE_REJECTED = "response_rejected"
    CUSTOMER_REPEATED_QUESTION = "customer_repeated_question"
    HUMAN_TAKEOVER = "human_takeover"
    BOOKING_SUCCESS = "booking_success"
    BOOKING_FAILURE = "booking_failure"
    KNOWLEDGE_GAP_CREATED = "knowledge_gap_created"
    KNOWLEDGE_GAP_RESOLVED = "knowledge_gap_resolved"


@dataclass
class OutcomeSignalRecord:
    """Individual recorded outcome signal with correlation metadata."""

    signal: OutcomeSignal
    signal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: Optional[str] = None
    thread_id: Optional[str] = None
    tenant_id: str = "default"
    account_key: str = "primary"
    customer_hash: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Coerce string enum and sanitize customer hash."""
        if isinstance(self.signal, str):
            self.signal = OutcomeSignal(self.signal)
        if self.customer_hash and PHONE_PATTERN.search(self.customer_hash):
            self.customer_hash = hash_sensitive_value(self.customer_hash)
        if self.thread_id and PHONE_PATTERN.search(self.thread_id):
            self.thread_id = f"thread-{hash_sensitive_value(self.thread_id)[:12]}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary."""
        return {
            "signal_id": self.signal_id,
            "signal": self.signal.value,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "trace_id": self.trace_id,
            "thread_id": self.thread_id,
            "tenant_id": self.tenant_id,
            "account_key": self.account_key,
            "customer_hash": self.customer_hash,
            "metadata": self.metadata,
        }


class OutcomeSignalTracker:
    """Thread-safe, capacity-bounded tracker for outcome signals."""

    def __init__(self, capacity: int = 5000) -> None:
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self._capacity = capacity
        self._signals: OrderedDict[str, OutcomeSignalRecord] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def record_signal(
        self,
        signal: Union[OutcomeSignal, str],
        trace_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        tenant_id: str = "default",
        account_key: str = "primary",
        customer_hash: Optional[str] = None,
        customer_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OutcomeSignalRecord:
        """Record an outcome signal with correlation metadata."""
        if isinstance(signal, str):
            signal = OutcomeSignal(signal)

        cust_hash = customer_hash
        if not cust_hash and customer_phone:
            cust_hash = hash_sensitive_value(customer_phone)

        record = OutcomeSignalRecord(
            signal=signal,
            trace_id=trace_id,
            thread_id=thread_id,
            tenant_id=tenant_id,
            account_key=account_key,
            customer_hash=cust_hash,
            metadata=dict(metadata or {}),
        )

        with self._lock:
            if len(self._signals) >= self._capacity:
                self._signals.popitem(last=False)
            self._signals[record.signal_id] = record

        return record

    def get_signal(self, signal_id: str) -> Optional[OutcomeSignalRecord]:
        """Get a specific signal record by signal_id."""
        with self._lock:
            return self._signals.get(signal_id)

    def list_signals(
        self,
        signal: Optional[Union[OutcomeSignal, str]] = None,
        trace_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        account_key: Optional[str] = None,
        limit: int = 100,
    ) -> List[OutcomeSignalRecord]:
        """List signals matching filter criteria, newest first."""
        target_signal = OutcomeSignal(signal) if isinstance(signal, str) else signal

        with self._lock:
            results: List[OutcomeSignalRecord] = []
            for record in reversed(self._signals.values()):
                if target_signal and record.signal != target_signal:
                    continue
                if trace_id and record.trace_id != trace_id:
                    continue
                if thread_id and record.thread_id != thread_id:
                    continue
                if tenant_id and record.tenant_id != tenant_id:
                    continue
                if account_key and record.account_key != account_key:
                    continue
                results.append(record)
                if len(results) >= limit:
                    break
            return results

    def count_by_signal(
        self,
        tenant_id: Optional[str] = None,
        account_key: Optional[str] = None,
    ) -> Dict[str, int]:
        """Get aggregate counts partitioned by signal type."""
        with self._lock:
            counts: Dict[str, int] = defaultdict(int)
            for record in self._signals.values():
                if tenant_id and record.tenant_id != tenant_id:
                    continue
                if account_key and record.account_key != account_key:
                    continue
                counts[record.signal.value] += 1
            return dict(counts)

    def count(self) -> int:
        """Total number of tracked signals."""
        with self._lock:
            return len(self._signals)

    def clear(self) -> None:
        """Clear all stored signals."""
        with self._lock:
            self._signals.clear()

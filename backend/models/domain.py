"""SQLAlchemy domain ORM models leaf module.

Contains all database entity definitions.
This module MUST NOT import anything from `backend.main`, `main`, or any route module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

try:
    from backend.core.database import Base
except ImportError:
    from core.database import Base


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        UniqueConstraint("sms_account_key", "customer_phone", name="uq_threads_sms_account_phone"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_phone = Column(String, nullable=False, index=True)
    sms_account_key = Column(String, default="primary", nullable=False, index=True)
    state = Column(String, default="auto-reply", nullable=False)  # auto-reply | needs-review | taken-over | escalated | resolved
    priority = Column(String, default="medium", nullable=False)  # low | medium | high
    assigned_agent_id = Column(String, nullable=True)
    sla_due_at = Column(DateTime, nullable=False)
    unread_count = Column(Integer, default=0, nullable=False)
    auto_reply_enabled = Column(Boolean, default=True, nullable=False)
    pinned = Column(Boolean, default=False, nullable=False)
    pending_slots = Column(Text, nullable=True)  # Legacy field; availability options are never retained.
    pending_booking = Column(Text, nullable=True)  # JSON proposal awaiting explicit customer confirmation
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    messages = relationship("Message", back_populates="thread", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="thread", cascade="all, delete-orphan")
    events = relationship("ThreadEvent", back_populates="thread", cascade="all, delete-orphan")


class BlockedContact(Base):
    __tablename__ = "blocked_contacts"
    __table_args__ = (
        UniqueConstraint("sms_account_key", "customer_phone", name="uq_blocked_contacts_account_phone"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sms_account_key = Column(String, nullable=False, index=True)
    customer_phone = Column(String, nullable=False, index=True)
    blocked_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # customer | agent | system
    text = Column(Text, nullable=False)
    provider_message_id = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)

    thread = relationship("Thread", back_populates="messages")


class InboundWebhookReceipt(Base):
    """Atomic claim preventing a provider webhook from being processed twice."""
    __tablename__ = "inbound_webhook_receipts"

    provider_message_id = Column(String, primary_key=True)
    from_phone = Column(String, nullable=True)
    received_at = Column(DateTime, nullable=False)
    claimed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Note(Base):
    __tablename__ = "notes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)

    thread = relationship("Thread", back_populates="notes")


class ThreadEvent(Base):
    __tablename__ = "thread_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String, nullable=False)
    agent_id = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
    meta = Column(Text, nullable=True)

    thread = relationship("Thread", back_populates="events")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    summary = Column(String, nullable=False)
    customer_phone = Column(String, nullable=True)
    sms_account_key = Column(String, nullable=True, index=True)
    thread_id = Column(String, nullable=True, index=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, default="scheduled", nullable=False)
    notes = Column(Text, nullable=True)
    amount = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ArrivalSession(Base):
    """Reusable customer arrival link with an idempotent check-in action."""
    __tablename__ = "arrival_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    booking_id = Column(String, nullable=False, index=True)
    thread_id = Column(String, ForeignKey("threads.id", ondelete="SET NULL"), nullable=True, index=True)
    sms_account_key = Column(String, nullable=True, index=True)
    invite_token_hash = Column(String, nullable=False, unique=True, index=True)
    client_token_hash = Column(String, nullable=True, unique=True, index=True)
    arrival_event_id = Column(String, nullable=True, unique=True, index=True)
    status = Column(String, nullable=False, default="invited", index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    activated_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True, index=True)
    last_alert_at = Column(DateTime, nullable=True)
    next_alert_at = Column(DateTime, nullable=True, index=True)
    alert_count = Column(Integer, nullable=False, default=0)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class ArrivalChatMessage(Base):
    __tablename__ = "arrival_chat_messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("arrival_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    sender = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class PushSubscription(Base):
    """An admin device authorized to receive operational Web Push alerts."""
    __tablename__ = "push_subscriptions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    user_agent = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    failure_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_success_at = Column(DateTime, nullable=True)


class OperationsChatMessage(Base):
    """Persistent, admin-only conversation with the operations adviser."""
    __tablename__ = "operations_chat_messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    role = Column(String, nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class OperationsAction(Base):
    """Audited, narrowly scoped maintenance action proposed by Operations AI."""
    __tablename__ = "operations_actions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    action_type = Column(String, nullable=False)
    payload = Column(Text, nullable=False, default="{}")
    reason = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    executed_at = Column(DateTime, nullable=True)


class OperationsMemory(Base):
    """Durable, non-secret operating knowledge curated by Operations AI."""
    __tablename__ = "operations_memories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    category = Column(String, nullable=False, default="behavior", index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    evidence = Column(Text, nullable=False, default="")
    active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class OperationsAgentRun(Base):
    """One authenticated, bounded autonomous Operations Console run."""
    __tablename__ = "operations_agent_runs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column(String, nullable=False, unique=True, index=True)
    actor = Column(String, nullable=False)
    objective = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="starting", index=True)
    step_count = Column(Integer, nullable=False, default=0)
    max_steps = Column(Integer, nullable=False, default=50)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    final_summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class OperationsAgentEvent(Base):
    """Ordered, replayable and redacted output from an Operations Console run."""
    __tablename__ = "operations_agent_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_operations_agent_event_sequence"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String, ForeignKey("operations_agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    message = Column(Text, nullable=False, default="")
    step = Column(Integer, nullable=True)
    meta = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


__all__ = [
    "Thread",
    "BlockedContact",
    "Message",
    "InboundWebhookReceipt",
    "Note",
    "ThreadEvent",
    "CalendarEvent",
    "ArrivalSession",
    "ArrivalChatMessage",
    "PushSubscription",
    "OperationsChatMessage",
    "OperationsAction",
    "OperationsMemory",
    "OperationsAgentRun",
    "OperationsAgentEvent",
]

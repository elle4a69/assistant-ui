"""SQLAlchemy models for Knowledge subsystem.

Supports PostgreSQL (+ pgvector) and SQLite (portable fallback for local dev / tests).
"""

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.types import JSON, TypeDecorator

try:
    from pgvector.sqlalchemy import Vector as PgVector
    PGVECTOR_AVAILABLE = True
except ImportError:
    PgVector = None
    PGVECTOR_AVAILABLE = False


class PortableVector(TypeDecorator):
    """Platform-independent Vector type.

    Uses pgvector's VECTOR on PostgreSQL when available;
    falls back to JSON on SQLite and environments without pgvector.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = 1536, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dim = dim

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql" and PGVECTOR_AVAILABLE and PgVector is not None:
            return dialect.type_descriptor(PgVector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [float(x) for x in value]
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [float(x) for x in value]
        return value


VectorType = PortableVector(1536)

Base = declarative_base()


def utcnow() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class KnowledgeRecord(Base):
    """Primary knowledge record representing a canonical piece of knowledge."""

    __tablename__ = "knowledge_records"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(100), nullable=True, index=True, default="default")
    account_key = Column(String(50), nullable=False, index=True, default="primary")
    canonical_key = Column(String(255), nullable=False, index=True)
    knowledge_type = Column(String(50), nullable=False, default="policy")
    content = Column(Text, nullable=False)
    normalised_content = Column(Text, nullable=False, index=True)
    instruction = Column(Text, nullable=True)
    example_reply = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, index=True, default="active")
    retrieval_enabled = Column(Boolean, nullable=False, index=True, default=False)
    authority_level = Column(
        String(50),
        nullable=False,
        index=True,
        default="canonical_knowledge",
    )
    source_type = Column(String(100), nullable=False, default="manual_guidance")
    source_actor_id = Column(String(255), nullable=True)
    source_id = Column(String(255), nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    effective_from = Column(DateTime(timezone=True), nullable=True)
    effective_until = Column(DateTime(timezone=True), nullable=True)
    supersedes_id = Column(String(255), nullable=True, index=True)
    superseded_by_id = Column(String(255), nullable=True, index=True)
    revision = Column(Integer, nullable=False, default=1)
    embedding = Column(VectorType, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    evidence_items = relationship(
        "KnowledgeEvidence",
        back_populates="knowledge_record",
        cascade="all, delete-orphan",
    )
    aliases = relationship(
        "KnowledgeAlias",
        back_populates="knowledge_record",
        cascade="all, delete-orphan",
    )


    __table_args__ = (
        Index("ix_knowledge_records_tenant_account", "tenant_id", "account_key"),
        Index("ix_knowledge_records_canonical_status", "canonical_key", "status"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary."""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "account_key": self.account_key,
            "canonical_key": self.canonical_key,
            "knowledge_type": self.knowledge_type,
            "content": self.content,
            "normalised_content": self.normalised_content,
            "instruction": self.instruction,
            "example_reply": self.example_reply,
            "status": self.status,
            "retrieval_enabled": self.retrieval_enabled,
            "authority_level": self.authority_level,
            "source_type": self.source_type,
            "source_actor_id": self.source_actor_id,
            "source_id": self.source_id,
            "confidence": self.confidence,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_until": self.effective_until.isoformat() if self.effective_until else None,
            "supersedes_id": self.supersedes_id,
            "superseded_by_id": self.superseded_by_id,
            "revision": self.revision,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


class KnowledgeEvidence(Base):
    """Audit evidence linking knowledge to SMS messages, manual inputs, or curator runs."""

    __tablename__ = "knowledge_evidence"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    knowledge_id = Column(
        String(255),
        ForeignKey("knowledge_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type = Column(String(100), nullable=False)
    source_message_id = Column(String(255), nullable=True)
    source_thread_id = Column(String(255), nullable=True)
    source_actor_id = Column(String(255), nullable=True)
    original_text_reference = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # Relationships
    knowledge_record = relationship("KnowledgeRecord", back_populates="evidence_items")

    def to_dict(self) -> dict[str, Any]:
        """Convert evidence to dictionary."""
        return {
            "id": self.id,
            "knowledge_id": self.knowledge_id,
            "source_type": self.source_type,
            "source_message_id": self.source_message_id,
            "source_thread_id": self.source_thread_id,
            "source_actor_id": self.source_actor_id,
            "original_text_reference": self.original_text_reference,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class KnowledgeAlias(Base):
    """User utterances and question variants mapped to canonical knowledge."""

    __tablename__ = "knowledge_aliases"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    knowledge_id = Column(
        String(255),
        ForeignKey("knowledge_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    utterance = Column(String(500), nullable=False)
    normalised_utterance = Column(String(500), nullable=False, index=True)
    embedding = Column(VectorType, nullable=True)
    source = Column(String(100), nullable=False, default="customer_variant")
    usage_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # Relationships
    knowledge_record = relationship("KnowledgeRecord", back_populates="aliases")

    def to_dict(self) -> dict[str, Any]:
        """Convert alias to dictionary."""
        return {
            "id": self.id,
            "knowledge_id": self.knowledge_id,
            "utterance": self.utterance,
            "normalised_utterance": self.normalised_utterance,
            "embedding": self.embedding,
            "source": self.source,
            "usage_count": self.usage_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

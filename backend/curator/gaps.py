"""First-Class Knowledge Gaps and Question Clustering subsystem.

Provides persistence, semantic and meaning signature question clustering,
owner resolution workflow, and integration with the Knowledge Repository.
"""

from datetime import datetime, timezone
import re
from typing import Any, List, Optional, Tuple
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    or_,
    select,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.types import JSON

from backend.curator.classifier import _knowledge_meaning_signature
from backend.curator.sanitizer import sanitise_reusable_knowledge_template
from backend.knowledge.models import (
    Base,
    KnowledgeRecord,
    PortableVector,
    utcnow,
)
from backend.knowledge.repository import KnowledgeRepository
from backend.knowledge.retrieval import (
    EmbeddingService,
    cosine_similarity,
    find_similar_knowledge_records,
)

# Canonical synonym dictionary for question clustering
QUESTION_SYNONYMS = {
    # Companions / guests
    "friend": "companion",
    "friends": "companion",
    "mate": "companion",
    "mates": "companion",
    "buddy": "companion",
    "buddies": "companion",
    "partner": "companion",
    "pal": "companion",
    "pals": "companion",
    "guest": "companion",
    "guests": "companion",
    "plusone": "companion",
    "plus_one": "companion",
    "person": "companion",
    "someone": "companion",
    # Bringing / attending
    "bring": "bring",
    "brings": "bring",
    "bringing": "bring",
    "brought": "bring",
    "come": "bring",
    "comes": "bring",
    "coming": "bring",
    "came": "bring",
    "join": "bring",
    "joins": "bring",
    "joining": "bring",
    "joined": "bring",
    "accompany": "bring",
    "accompanies": "bring",
    "accompanying": "bring",
    "accompanied": "bring",
    "tag": "bring",
    "tags": "bring",
    "along": "bring",
    "attend": "bring",
    "attends": "bring",
    # Parking
    "parking": "parking",
    "park": "parking",
    "carpark": "parking",
    "garage": "parking",
    "car": "parking",
    "vehicle": "parking",
    # Cost / price
    "cost": "price",
    "price": "price",
    "fee": "price",
    "fees": "price",
    "charge": "price",
    "charges": "price",
    "rate": "price",
    "rates": "price",
    # Cancellation / reschedule
    "cancel": "cancel",
    "cancelling": "cancel",
    "cancellation": "cancel",
    "reschedule": "cancel",
    "rescheduling": "cancel",
    "refund": "cancel",
    # Wifi
    "wifi": "wifi",
    "wi-fi": "wifi",
    "internet": "wifi",
    # Hours
    "hours": "hours",
    "opening": "hours",
    "closing": "hours",
    # Late
    "late": "late",
    "delay": "late",
    "delayed": "late",
}

QUESTION_STOP_WORDS = frozenset({
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "my", "your",
    "i", "we", "you", "me", "us", "it", "is", "am", "are", "can", "could", "would",
    "will", "may", "do", "does", "did", "have", "has", "had", "be", "been", "being",
    "there", "here", "any", "some", "what", "which", "how", "when", "where", "why",
    "if", "okay", "allowed", "possible", "please", "available", "have",
})


def _canonicalize_key(value: Any) -> str:
    """Derive a URL-friendly canonical key from a title or question."""
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return normalized[:160]


def _question_meaning_signature(text: str) -> str:
    """Extract a canonical semantic meaning signature for customer questions.

    Normalizes synonyms, colloquialisms, and conversational stop words.
    """
    cleaned = re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold())
    tokens = [QUESTION_SYNONYMS.get(w, w) for w in cleaned.split() if w not in QUESTION_STOP_WORDS]
    if not tokens:
        return ""
    unique_tokens = sorted(set(tokens))
    return " ".join(unique_tokens)


def matches_meaning_signature(q1: str, q2: str) -> bool:
    """Check if two questions match the same semantic meaning signature."""
    sig1 = _question_meaning_signature(q1)
    sig2 = _question_meaning_signature(q2)
    if sig1 and sig2 and sig1 == sig2:
        return True

    base1 = _knowledge_meaning_signature(q1)
    base2 = _knowledge_meaning_signature(q2)
    if base1 and base2 and base1 == base2:
        return True

    return False


def _format_owner_question(question: str) -> str:
    """Generate a clear, concise question prompt for the business owner."""
    q = (question or "").strip()
    if not q:
        return "How should we answer this question?"
    if not q.endswith("?"):
        q = f"{q}?"
    q = q[0].upper() + q[1:]
    return f"How should we respond to: '{q}'"


class KnowledgeGap(Base):
    """Primary knowledge gap entity tracking unanswered customer questions and clustering."""

    __tablename__ = "knowledge_gaps"

    id = Column(String(255), primary_key=True, default=lambda: f"kg-{uuid.uuid4()}")
    tenant_id = Column(String(100), nullable=False, default="default", index=True)
    account_key = Column(String(50), nullable=False, default="primary", index=True)
    canonical_question = Column(Text, nullable=False)
    intent = Column(String(100), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="awaiting_owner", index=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    occurrence_count = Column(Integer, nullable=False, default=1)
    customer_count = Column(Integer, nullable=False, default=1)
    similar_existing_knowledge_ids = Column(JSON, nullable=False, default=list)
    example_message_ids = Column(JSON, nullable=False, default=list)
    example_questions = Column(JSON, nullable=False, default=list)
    owner_question = Column(Text, nullable=False)
    owner_answer = Column(Text, nullable=True)
    resolved_by_knowledge_id = Column(String(255), nullable=True, index=True)
    confidence = Column(Float, nullable=False, default=1.0)
    embedding = Column(PortableVector(1536), nullable=True)
    customer_phones = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_knowledge_gaps_tenant_account_status", "tenant_id", "account_key", "status"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert gap to dictionary representation."""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "account_key": self.account_key,
            "canonical_question": self.canonical_question,
            "intent": self.intent,
            "status": self.status,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "occurrence_count": self.occurrence_count,
            "customer_count": self.customer_count,
            "similar_existing_knowledge_ids": list(self.similar_existing_knowledge_ids or []),
            "example_message_ids": list(self.example_message_ids or []),
            "example_questions": list(self.example_questions or []),
            "owner_question": self.owner_question,
            "owner_answer": self.owner_answer,
            "resolved_by_knowledge_id": self.resolved_by_knowledge_id,
            "confidence": self.confidence,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class KnowledgeGapManager:
    """Manager service for recording knowledge gaps, clustering questions, and resolving gaps."""

    def __init__(
        self,
        session_factory: Any,
        embedding_service: Optional[EmbeddingService] = None,
        repository: Optional[KnowledgeRepository] = None,
    ):
        """Initialize KnowledgeGapManager.

        Args:
            session_factory: SQLAlchemy Session instance or sessionmaker factory.
            embedding_service: EmbeddingService instance.
            repository: Optional KnowledgeRepository instance.
        """
        if isinstance(session_factory, sessionmaker):
            self._session_factory = session_factory
            self._session = None
        elif isinstance(session_factory, Session):
            self._session = session_factory
            self._session_factory = None
        else:
            self._session_factory = session_factory
            self._session = None

        self.embedding_service = embedding_service or EmbeddingService()
        self._repository = repository

    def _get_session(self) -> Session:
        if self._session is not None:
            return self._session
        return self._session_factory()

    def record_unanswered_question(
        self,
        question: str,
        account_key: str = "primary",
        tenant_id: str = "default",
        customer_phone: Optional[str] = None,
        message_id: Optional[str] = None,
        intent: Optional[str] = None,
        owner_question: Optional[str] = None,
        repository: Optional[KnowledgeRepository] = None,
    ) -> KnowledgeGap:
        """Record an unanswered customer question, clustering into existing open gaps if similar.

        Args:
            question: Raw customer question text.
            account_key: Account or line identifier (e.g. 'primary', 'secondary').
            tenant_id: Tenant identifier.
            customer_phone: Sender phone number to track unique customer counts.
            message_id: Inbound message ID for audit reference.
            intent: Optional classified intent.
            owner_question: Optional explicit owner question override.
            repository: Optional KnowledgeRepository for similarity search.

        Returns:
            KnowledgeGap: Existing clustered gap or newly created gap.
        """
        clean_question = str(question or "").strip()
        if not clean_question:
            raise ValueError("Question cannot be empty.")

        question_vec = self.embedding_service.get_embedding(clean_question)

        session = self._get_session()
        close_on_exit = self._session is None
        try:
            stmt = (
                select(KnowledgeGap)
                .where(
                    KnowledgeGap.tenant_id == tenant_id,
                    KnowledgeGap.account_key == account_key,
                    KnowledgeGap.status.in_(["open", "awaiting_owner"]),
                )
                .order_by(KnowledgeGap.last_seen_at.desc())
            )
            candidates = list(session.scalars(stmt).all())

            best_gap: Optional[KnowledgeGap] = None
            best_score: float = -1.0

            for gap in candidates:
                gap_vec = gap.embedding
                if not gap_vec:
                    gap_vec = self.embedding_service.get_embedding(gap.canonical_question)
                sim = max(0.0, cosine_similarity(question_vec, gap_vec))

                meaning_match = (
                    matches_meaning_signature(clean_question, gap.canonical_question)
                    or any(matches_meaning_signature(clean_question, str(eq)) for eq in (gap.example_questions or []))
                )

                if sim >= 0.80 or meaning_match:
                    score = max(sim, 0.85 if meaning_match else sim)
                    if score > best_score:
                        best_score = score
                        best_gap = gap

            now = utcnow()

            if best_gap is not None:
                # Clustered into existing gap
                best_gap.occurrence_count = (best_gap.occurrence_count or 1) + 1
                best_gap.last_seen_at = now
                best_gap.updated_at = now

                # Track unique customer_phone
                if customer_phone:
                    phones = list(best_gap.customer_phones or [])
                    if customer_phone not in phones:
                        phones.append(customer_phone)
                        best_gap.customer_phones = phones
                        best_gap.customer_count = max(best_gap.customer_count, len(phones))
                        flag_modified(best_gap, "customer_phones")

                # Track message_id
                if message_id:
                    msg_ids = list(best_gap.example_message_ids or [])
                    if message_id not in msg_ids:
                        msg_ids.append(message_id)
                        best_gap.example_message_ids = msg_ids
                        flag_modified(best_gap, "example_message_ids")

                # Track example question (max 20)
                examples = list(best_gap.example_questions or [])
                if clean_question not in examples:
                    examples.append(clean_question)
                if len(examples) > 20:
                    examples = examples[-20:]
                best_gap.example_questions = examples
                flag_modified(best_gap, "example_questions")

                session.commit()
                session.refresh(best_gap)
                if close_on_exit:
                    session.expunge(best_gap)
                return best_gap

            # No matching gap found: discover similar existing knowledge records
            active_repo = repository or self._repository or KnowledgeRepository(session)
            similar_records = []
            try:
                similar_records = find_similar_knowledge_records(
                    text=clean_question,
                    repository=active_repo,
                    threshold=0.60,
                    limit=5,
                    embedding_service=self.embedding_service,
                )
            except Exception:
                similar_records = []
            similar_ids = [rec.id for rec, _ in similar_records]

            formatted_owner_q = owner_question or _format_owner_question(clean_question)

            new_gap = KnowledgeGap(
                id=f"kg-{uuid.uuid4()}",
                tenant_id=tenant_id,
                account_key=account_key,
                canonical_question=clean_question,
                intent=intent,
                status="awaiting_owner",
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
                customer_count=1,
                similar_existing_knowledge_ids=similar_ids,
                example_message_ids=[message_id] if message_id else [],
                example_questions=[clean_question],
                owner_question=formatted_owner_q,
                owner_answer=None,
                resolved_by_knowledge_id=None,
                confidence=1.0,
                embedding=question_vec,
                customer_phones=[customer_phone] if customer_phone else [],
                created_at=now,
                updated_at=now,
            )
            session.add(new_gap)
            session.commit()
            session.refresh(new_gap)
            if close_on_exit:
                session.expunge(new_gap)
            return new_gap
        except Exception:
            session.rollback()
            raise
        finally:
            if close_on_exit:
                session.close()

    def resolve_gap(
        self,
        gap_id: str,
        owner_answer: str,
        repository: KnowledgeRepository,
        author_id: str = "owner",
    ) -> KnowledgeRecord:
        """Resolve a knowledge gap with an owner answer, creating canonical knowledge and aliases.

        Args:
            gap_id: ID of the gap to resolve.
            owner_answer: Owner's answer text.
            repository: KnowledgeRepository to persist canonical knowledge and aliases.
            author_id: Identifier of the answering actor (default 'owner').

        Returns:
            KnowledgeRecord: Persisted authoritative KnowledgeRecord.
        """
        clean_answer = str(owner_answer or "").strip()
        if not clean_answer:
            raise ValueError("Owner answer cannot be empty.")

        session = self._get_session()
        close_on_exit = self._session is None
        try:
            gap = session.get(KnowledgeGap, gap_id)
            if not gap:
                raise ValueError(f"Knowledge gap with id '{gap_id}' not found.")
            if gap.status == "resolved":
                raise ValueError(f"Knowledge gap '{gap_id}' is already resolved.")
            if gap.status not in {"open", "awaiting_owner", "answered"}:
                raise ValueError(f"Knowledge gap '{gap_id}' cannot be resolved from status '{gap.status}'.")

            # Sanitize owner answer and canonical question template
            sanitized_answer = sanitise_reusable_knowledge_template(clean_answer, gap.account_key)
            if sanitized_answer is None and gap.account_key not in {"primary", "secondary", "shared"}:
                sanitized_answer = sanitise_reusable_knowledge_template(clean_answer, "shared")
            if sanitized_answer is None:
                raise ValueError(
                    f"Owner answer contains unsafe or volatile details and could not be sanitized for account '{gap.account_key}'."
                )

            sanitized_question = sanitise_reusable_knowledge_template(gap.canonical_question, gap.account_key)
            if sanitized_question is None and gap.account_key not in {"primary", "secondary", "shared"}:
                sanitized_question = sanitise_reusable_knowledge_template(gap.canonical_question, "shared")
            if sanitized_question is None:
                sanitized_question = gap.canonical_question

            canonical_key = _canonicalize_key(sanitized_question)
            if not canonical_key:
                canonical_key = f"gap-{gap.id}"

            record_data = {
                "tenant_id": gap.tenant_id,
                "account_key": gap.account_key,
                "canonical_key": canonical_key,
                "knowledge_type": "policy",
                "content": sanitized_answer,
                "normalised_content": sanitized_answer.casefold(),
                "instruction": f"Answer question: {gap.canonical_question}",
                "example_reply": sanitized_answer,
                "status": "active",
                "retrieval_enabled": True,
                "authority_level": "owner_instruction",
                "source_type": "knowledge_gap_resolution",
                "source_actor_id": author_id,
                "source_id": gap.id,
                "confidence": 1.0,
                "embedding": self.embedding_service.get_embedding(sanitized_answer),
            }
            evidence_data = {
                "source_type": "knowledge_gap_resolution",
                "source_actor_id": author_id,
                "source_message_id": gap.example_message_ids[0] if gap.example_message_ids else None,
                "original_text_reference": gap.canonical_question,
            }
            record = repository.save_record(record_data, evidence_data=evidence_data)

            # Attach canonical question and all example question variants as KnowledgeAlias
            aliases_to_attach: set[str] = set()
            if gap.canonical_question and gap.canonical_question.strip():
                aliases_to_attach.add(gap.canonical_question.strip())
            for eq in (gap.example_questions or []):
                if eq and str(eq).strip():
                    aliases_to_attach.add(str(eq).strip())

            for utterance in aliases_to_attach:
                repository.add_alias(
                    knowledge_id=record.id,
                    utterance=utterance,
                    source="customer_variant",
                )

            # Update gap entity
            now = utcnow()
            gap.status = "resolved"
            gap.owner_answer = clean_answer
            gap.resolved_by_knowledge_id = record.id
            gap.updated_at = now

            session.commit()
            session.refresh(gap)
            if close_on_exit:
                session.expunge(gap)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            if close_on_exit:
                session.close()

    def list_gaps(
        self,
        account_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[KnowledgeGap]:
        """List knowledge gaps with optional filtering.

        Args:
            account_key: Optional account_key filter.
            tenant_id: Optional tenant_id filter.
            status: Optional status filter.
            limit: Maximum items to return.
            offset: Query offset.

        Returns:
            List[KnowledgeGap]: List of matching gaps ordered by last_seen_at desc.
        """
        session = self._get_session()
        close_on_exit = self._session is None
        try:
            stmt = select(KnowledgeGap)
            if tenant_id is not None:
                stmt = stmt.where(KnowledgeGap.tenant_id == tenant_id)
            if account_key is not None:
                stmt = stmt.where(KnowledgeGap.account_key == account_key)
            if status is not None:
                stmt = stmt.where(KnowledgeGap.status == status)

            stmt = stmt.order_by(KnowledgeGap.last_seen_at.desc()).limit(limit).offset(offset)
            items = list(session.scalars(stmt).all())
            if close_on_exit:
                session.expunge_all()
            return items
        finally:
            if close_on_exit:
                session.close()

    def get_gap(self, gap_id: str) -> Optional[KnowledgeGap]:
        """Retrieve a knowledge gap by ID.

        Args:
            gap_id: Primary key id of the gap.

        Returns:
            Optional[KnowledgeGap]: The matching gap or None.
        """
        session = self._get_session()
        close_on_exit = self._session is None
        try:
            gap = session.get(KnowledgeGap, gap_id)
            if gap and close_on_exit:
                session.expunge(gap)
            return gap
        finally:
            if close_on_exit:
                session.close()

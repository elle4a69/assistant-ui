"""Unit tests for Phase 5: First-Class Knowledge Gaps & Question Clustering.

Tests:
1. First unknown question creates a new gap.
2. Equivalent / paraphrased questions cluster into existing gap (occurrences & examples tracked).
3. Unrelated questions create separate gaps.
4. Owner answer resolves the gap, creates canonical KnowledgeRecord, attaches aliases, links resolved_by_knowledge_id.
5. Tenant and account isolation.
6. Resolving an already resolved gap or missing gap raises appropriate errors.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.curator.gaps import KnowledgeGap, KnowledgeGapManager
from backend.knowledge.models import (
    Base,
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeRecord,
)
from backend.knowledge.repository import KnowledgeRepository
from backend.knowledge.retrieval import EmbeddingService


@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def repo(db_session):
    """KnowledgeRepository bound to the test session."""
    return KnowledgeRepository(db_session)


@pytest.fixture
def emb_service():
    """Deterministic offline embedding service."""
    return EmbeddingService(offline_only=True)


@pytest.fixture
def gap_manager(db_session, emb_service, repo):
    """KnowledgeGapManager instance."""
    return KnowledgeGapManager(
        session_factory=db_session,
        embedding_service=emb_service,
        repository=repo,
    )


# ---------------------------------------------------------------------------
# Test 1: First unknown question creates a new gap
# ---------------------------------------------------------------------------


def test_first_unknown_question_creates_new_gap(gap_manager, db_session):
    """Verify that the first unknown question creates a new KnowledgeGap in awaiting_owner status."""
    gap = gap_manager.record_unanswered_question(
        question="What is your cancellation policy?",
        account_key="primary",
        tenant_id="default",
        customer_phone="+61411222333",
        message_id="msg-101",
    )

    assert gap is not None
    assert gap.id.startswith("kg-")
    assert gap.canonical_question == "What is your cancellation policy?"
    assert gap.status == "awaiting_owner"
    assert gap.occurrence_count == 1
    assert gap.customer_count == 1
    assert gap.example_message_ids == ["msg-101"]
    assert gap.example_questions == ["What is your cancellation policy?"]
    assert gap.owner_question is not None
    assert len(gap.owner_question) > 0
    assert gap.owner_answer is None
    assert gap.resolved_by_knowledge_id is None
    assert gap.confidence == 1.0
    assert gap.embedding is not None
    assert len(gap.embedding) == 1536

    # Verify to_dict serialization
    d = gap.to_dict()
    assert d["id"] == gap.id
    assert d["canonical_question"] == "What is your cancellation policy?"
    assert d["status"] == "awaiting_owner"
    assert d["occurrence_count"] == 1
    assert d["customer_count"] == 1
    assert d["example_message_ids"] == ["msg-101"]
    assert d["example_questions"] == ["What is your cancellation policy?"]
    assert d["created_at"] is not None
    assert d["updated_at"] is not None


# ---------------------------------------------------------------------------
# Test 2: Paraphrased / equivalent questions cluster into existing gap
# ---------------------------------------------------------------------------


def test_equivalent_paraphrased_questions_cluster(gap_manager, db_session):
    """Paraphrased questions cluster into existing gap, tracking occurrences and unique customers."""
    # First question
    gap1 = gap_manager.record_unanswered_question(
        question="Can I bring a friend?",
        account_key="primary",
        tenant_id="default",
        customer_phone="+61400111222",
        message_id="msg-001",
    )
    initial_id = gap1.id

    # Second question: semantic paraphrase from a different customer
    gap2 = gap_manager.record_unanswered_question(
        question="Can my mate come?",
        account_key="primary",
        tenant_id="default",
        customer_phone="+61400333444",
        message_id="msg-002",
    )

    assert gap2.id == initial_id
    assert gap2.occurrence_count == 2
    assert gap2.customer_count == 2
    assert "msg-001" in gap2.example_message_ids
    assert "msg-002" in gap2.example_message_ids
    assert "Can I bring a friend?" in gap2.example_questions
    assert "Can my mate come?" in gap2.example_questions

    # Only 1 gap should exist in the repository for this account
    all_gaps = gap_manager.list_gaps(account_key="primary")
    assert len(all_gaps) == 1

    # Third question: duplicate customer (+61400111222) asking another variant
    gap3 = gap_manager.record_unanswered_question(
        question="Is it okay if a buddy joins me?",
        account_key="primary",
        tenant_id="default",
        customer_phone="+61400111222",
        message_id="msg-003",
    )

    assert gap3.id == initial_id
    assert gap3.occurrence_count == 3
    # Customer count stays 2 because +61400111222 was already counted
    assert gap3.customer_count == 2
    assert len(gap3.example_message_ids) == 3
    assert len(gap3.example_questions) == 3


# ---------------------------------------------------------------------------
# Test 3: Unrelated question creates a separate gap
# ---------------------------------------------------------------------------


def test_unrelated_questions_create_separate_gaps(gap_manager, db_session):
    """Unrelated questions create distinct KnowledgeGap records."""
    gap_parking = gap_manager.record_unanswered_question(
        question="Where can I park my car?",
        account_key="primary",
        tenant_id="default",
    )

    gap_vegan = gap_manager.record_unanswered_question(
        question="Do you have vegan refreshments?",
        account_key="primary",
        tenant_id="default",
    )

    assert gap_parking.id != gap_vegan.id
    assert gap_parking.canonical_question == "Where can I park my car?"
    assert gap_vegan.canonical_question == "Do you have vegan refreshments?"

    all_gaps = gap_manager.list_gaps(account_key="primary")
    assert len(all_gaps) == 2
    gap_ids = {g.id for g in all_gaps}
    assert gap_parking.id in gap_ids
    assert gap_vegan.id in gap_ids


# ---------------------------------------------------------------------------
# Test 4: Owner answer resolves gap and creates canonical KnowledgeRecord
# ---------------------------------------------------------------------------


def test_owner_answer_resolves_gap_and_creates_canonical_knowledge(
    gap_manager, repo, db_session
):
    """Resolving a gap creates canonical knowledge, attaches aliases, and links back to the gap."""
    # Seed gap with initial question and paraphrase
    gap = gap_manager.record_unanswered_question(
        question="Can I bring a friend?",
        account_key="primary",
        tenant_id="default",
        customer_phone="+61400111222",
        message_id="msg-001",
    )
    gap_manager.record_unanswered_question(
        question="Can my mate come?",
        account_key="primary",
        tenant_id="default",
        customer_phone="+61400333444",
        message_id="msg-002",
    )

    # Owner resolves the gap
    owner_answer = "Yes, you are welcome to bring a friend or guest along to your appointment."
    record = gap_manager.resolve_gap(
        gap_id=gap.id,
        owner_answer=owner_answer,
        repository=repo,
        author_id="owner-frank",
    )

    # Verify KnowledgeRecord creation
    assert isinstance(record, KnowledgeRecord)
    assert record.content == owner_answer
    assert record.authority_level == "owner_instruction"
    assert record.source_type == "knowledge_gap_resolution"
    assert record.source_actor_id == "owner-frank"
    assert record.retrieval_enabled is True
    assert record.status == "active"
    assert record.embedding is not None

    # Verify KnowledgeAlias creation for canonical question and variants
    aliases = repo.get_aliases(record.id)
    alias_utterances = {a.utterance for a in aliases}
    assert "Can I bring a friend?" in alias_utterances
    assert "Can my mate come?" in alias_utterances

    # Verify gap status update
    refreshed_gap = gap_manager.get_gap(gap.id)
    assert refreshed_gap is not None
    assert refreshed_gap.status == "resolved"
    assert refreshed_gap.owner_answer == owner_answer
    assert refreshed_gap.resolved_by_knowledge_id == record.id


# ---------------------------------------------------------------------------
# Test 5: Tenant and account isolation
# ---------------------------------------------------------------------------


def test_tenant_and_account_isolation(gap_manager, db_session):
    """Gaps must be isolated across tenants and account keys even for identical questions."""
    q = "What are your opening hours?"

    # Primary line, tenant alpha
    gap_alpha_pri = gap_manager.record_unanswered_question(
        question=q,
        account_key="primary",
        tenant_id="tenant-alpha",
    )

    # Secondary line, tenant alpha
    gap_alpha_sec = gap_manager.record_unanswered_question(
        question=q,
        account_key="secondary",
        tenant_id="tenant-alpha",
    )

    # Primary line, tenant beta
    gap_beta_pri = gap_manager.record_unanswered_question(
        question=q,
        account_key="primary",
        tenant_id="tenant-beta",
    )

    # All three must be distinct gaps
    assert gap_alpha_pri.id != gap_alpha_sec.id
    assert gap_alpha_pri.id != gap_beta_pri.id
    assert gap_alpha_sec.id != gap_beta_pri.id

    # Filtered listings must respect scope
    alpha_pri = gap_manager.list_gaps(account_key="primary", tenant_id="tenant-alpha")
    assert len(alpha_pri) == 1
    assert alpha_pri[0].id == gap_alpha_pri.id

    alpha_sec = gap_manager.list_gaps(account_key="secondary", tenant_id="tenant-alpha")
    assert len(alpha_sec) == 1
    assert alpha_sec[0].id == gap_alpha_sec.id

    beta_pri = gap_manager.list_gaps(account_key="primary", tenant_id="tenant-beta")
    assert len(beta_pri) == 1
    assert beta_pri[0].id == gap_beta_pri.id


# ---------------------------------------------------------------------------
# Test 6: Resolving an already resolved gap or missing gap raises appropriate errors
# ---------------------------------------------------------------------------


def test_resolve_gap_error_handling(gap_manager, repo, db_session):
    """Appropriate exceptions are raised for invalid gap operations."""
    # Nonexistent gap ID
    with pytest.raises(ValueError, match="not found"):
        gap_manager.resolve_gap(
            gap_id="kg-nonexistent",
            owner_answer="Some answer",
            repository=repo,
        )

    # Empty question
    with pytest.raises(ValueError, match="cannot be empty"):
        gap_manager.record_unanswered_question("   ", account_key="primary")

    # Create a valid gap
    gap = gap_manager.record_unanswered_question(
        question="Do you allow pets?",
        account_key="primary",
    )

    # Empty owner answer
    with pytest.raises(ValueError, match="cannot be empty"):
        gap_manager.resolve_gap(
            gap_id=gap.id,
            owner_answer="",
            repository=repo,
        )

    # First resolution succeeds
    gap_manager.resolve_gap(
        gap_id=gap.id,
        owner_answer="Yes, guide dogs and small pets in carriers are welcome.",
        repository=repo,
    )

    # Second resolution must fail as already resolved
    with pytest.raises(ValueError, match="already resolved"):
        gap_manager.resolve_gap(
            gap_id=gap.id,
            owner_answer="Updated answer.",
            repository=repo,
        )

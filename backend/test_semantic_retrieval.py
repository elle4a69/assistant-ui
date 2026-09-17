"""Unit tests for Phase 4: Semantic Candidate Discovery & Hybrid Retrieval.

Governing Principles tested:
1. Embeddings locate candidates; embeddings do not determine authority.
2. Similarity scores never trigger auto-merging.
3. Fail-closed conflict handling: conflicting candidates exclude both, returning None.
4. Separation of concerns: authoritative knowledge vs style few-shot pairs.
"""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.curator.authority import knowledge_authority_reason_codes
from backend.knowledge.models import (
    Base,
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeRecord,
)
from backend.knowledge.repository import KnowledgeRepository
from backend.knowledge.retrieval import (
    AUTHORITY_HIERARCHY,
    EmbeddingService,
    HybridKnowledgeRetriever,
    cosine_similarity,
    find_similar_knowledge_records,
)


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
    """Offline EmbeddingService instance."""
    return EmbeddingService(offline_only=True)


@pytest.fixture
def retriever(repo, emb_service):
    """HybridKnowledgeRetriever instance configured for offline testing."""
    return HybridKnowledgeRetriever(repository=repo, embedding_service=emb_service)


# ---------------------------------------------------------------------------
# 1. Cosine Similarity Math Tests
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical_vectors():
    """Identical vectors should yield cosine similarity of 1.0."""
    vec = [1.0, 2.0, 3.0, 4.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)
    assert EmbeddingService.cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    """Orthogonal vectors should yield cosine similarity of 0.0."""
    vec_a = [1.0, 0.0]
    vec_b = [0.0, 1.0]
    assert cosine_similarity(vec_a, vec_b) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors():
    """Opposite vectors should yield cosine similarity of -1.0."""
    vec_a = [1.0, 0.0]
    vec_b = [-1.0, 0.0]
    assert cosine_similarity(vec_a, vec_b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_and_empty_vectors():
    """Zero or empty vectors should yield 0.0 safely without division by zero."""
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0  # Mismatched length
    assert cosine_similarity([float("nan"), 1.0], [1.0, 1.0]) == 0.0  # Non-finite NaN guard
    assert cosine_similarity([float("inf"), 1.0], [1.0, 1.0]) == 0.0  # Non-finite Inf guard


def test_cosine_similarity_known_angle():
    """Vectors [3, 4] and [4, 3] have dot=24, norm1=5, norm2=5 -> 24/25 = 0.96."""
    vec_a = [3.0, 4.0]
    vec_b = [4.0, 3.0]
    assert cosine_similarity(vec_a, vec_b) == pytest.approx(0.96)


# ---------------------------------------------------------------------------
# 2. Offline Embedding Fallback Tests
# ---------------------------------------------------------------------------


def test_offline_embedding_deterministic_and_normalized(emb_service):
    """Offline embedding generates deterministic, unit-normalized vectors."""
    assert emb_service.offline_only is True
    assert emb_service._resolve_client() is None
    text = "Cancellation policy: notice must be provided 24 hours in advance."
    vec1 = emb_service.get_embedding(text)
    vec2 = emb_service.get_embedding(text)

    # Fixed dimension (1536)
    assert len(vec1) == 1536
    # Pure determinism
    assert vec1 == vec2
    # Unit L2 norm (sum of squares is ~1.0)
    l2_norm = sum(x * x for x in vec1)
    assert l2_norm == pytest.approx(1.0, abs=1e-4)


def test_offline_embedding_distinct_and_empty(emb_service):
    """Different texts yield distinct vectors; empty text returns zeros."""
    vec_a = emb_service.get_embedding("Cancellation policy for salon appointments.")
    vec_b = emb_service.get_embedding("Pricing for men's haircut and beard trim.")
    assert vec_a != vec_b

    # Self similarity is 1.0
    assert cosine_similarity(vec_a, vec_a) == pytest.approx(1.0)

    # Empty / whitespace strings return zero vectors
    zero_vec = [0.0] * 1536
    assert emb_service.get_embedding("") == zero_vec
    assert emb_service.get_embedding("   ") == zero_vec


# ---------------------------------------------------------------------------
# 3. Hybrid Candidate Ranking & Authority Hierarchy
# ---------------------------------------------------------------------------


def test_hybrid_candidate_ranking_respects_authority_hierarchy(repo, retriever):
    """Governing Principle 1: Embeddings locate candidates, but do not determine authority.

    Owner instruction outranks customer assertion even when customer assertion has higher similarity.
    """
    query = "Can I cancel my haircut without paying any cancellation fee today?"

    # Customer assertion: crafted to have near-verbatim match to the query
    repo.save_record({
        "id": "rec-cust",
        "canonical_key": "cancellation-fee",
        "content": "Can I cancel my haircut without paying any cancellation fee today?",
        "authority_level": "customer_assertion",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "primary",
    })

    # Owner instruction: authoritative business policy, partial overlap
    repo.save_record({
        "id": "rec-owner",
        "canonical_key": "cancellation-fee",
        "content": "Cancellation policy requires 24 hours advance notice or a $50 fee applies.",
        "authority_level": "owner_instruction",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "primary",
    })

    candidates = retriever.find_candidates(query=query, account_key="primary", limit=5)

    assert len(candidates) >= 2
    # The top-ranked candidate MUST be the owner instruction due to authority level ranking
    assert candidates[0]["id"] == "rec-owner"
    assert candidates[0]["authority_level"] == "owner_instruction"
    assert candidates[0]["authority_rank"] == AUTHORITY_HIERARCHY["owner_instruction"]

    # Customer assertion ranks lower despite higher lexical/semantic similarity
    assert candidates[1]["id"] == "rec-cust"
    assert candidates[1]["authority_level"] == "customer_assertion"
    assert candidates[1]["authority_rank"] == AUTHORITY_HIERARCHY["customer_assertion"]
    assert candidates[1]["semantic_similarity"] >= candidates[0]["semantic_similarity"]


# ---------------------------------------------------------------------------
# 4. Paraphrase Candidate Discovery via Alias
# ---------------------------------------------------------------------------


def test_paraphrase_candidate_discovery_via_alias(repo, retriever):
    """Candidate discovery finds records matching user utterance variants via KnowledgeAlias."""
    # Create canonical knowledge record
    repo.save_record({
        "id": "rec-refund",
        "canonical_key": "refund-policy",
        "content": "Refund requests are processed within five business days to the original payment method.",
        "authority_level": "canonical_knowledge",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "primary",
    })

    # Attach alias with phrasing distinct from content
    repo.add_alias(
        knowledge_id="rec-refund",
        utterance="When do I get my money back?",
        source="customer_variant",
    )

    query = "When do I get my money back?"
    candidates = retriever.find_candidates(query=query, account_key="primary", limit=5)

    assert len(candidates) >= 1
    top_cand = candidates[0]
    assert top_cand["id"] == "rec-refund"
    assert "alias" in top_cand["matched_by"]

    # Authoritative fact retrieval returns the winning record
    fact = retriever.retrieve_authoritative_fact(query=query, account_key="primary")
    assert fact is not None
    assert fact["id"] == "rec-refund"
    assert "five business days" in fact["content"]


# ---------------------------------------------------------------------------
# 5. Fail-Closed Conflict Handling
# ---------------------------------------------------------------------------


def test_conflicting_active_records_result_in_none_fail_closed(repo, retriever):
    """Governing Principle 3: If two candidates conflict, authority excludes both, returning None."""
    query = "How much is a standard haircut?"

    # Two active, retrieval-enabled records on the SAME topic with CONFLICTING prices
    repo.save_record({
        "id": "rec-price-45",
        "canonical_key": "standard-haircut-price",
        "content": "Standard haircut price is $45.",
        "authority_level": "canonical_knowledge",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "primary",
    })
    repo.save_record({
        "id": "rec-price-60",
        "canonical_key": "standard-haircut-price",
        "content": "Standard haircut price is $60.",
        "authority_level": "canonical_knowledge",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "primary",
    })

    fact = retriever.retrieve_authoritative_fact(query=query, account_key="primary")

    # Fail closed: must return None rather than guessing between conflicting prices
    assert fact is None

    # Diagnostic reason codes should reflect knowledge_excluded_conflict
    codes = [item["code"] for item in knowledge_authority_reason_codes()]
    assert "knowledge_excluded_conflict" in codes


# ---------------------------------------------------------------------------
# 6. Expired Successor Does Not Restore Predecessor
# ---------------------------------------------------------------------------


def test_expired_successor_does_not_restore_predecessor(repo, retriever):
    """A successor is a revocation boundary: if expired, predecessor cannot reappear."""
    now = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    query = "What are your business hours?"

    # Predecessor: valid in isolation, revision 1
    repo.save_record({
        "id": "rec-hours-v1",
        "canonical_key": "business-hours",
        "content": "Regular hours are 9am to 5pm weekdays.",
        "authority_level": "owner_instruction",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "primary",
        "revision": 1,
    })

    # Successor: supersedes v1, revision 2, expired in 2022
    repo.save_record({
        "id": "rec-hours-v2",
        "canonical_key": "business-hours",
        "content": "Holiday hours are 10am to 2pm weekdays.",
        "authority_level": "owner_instruction",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "primary",
        "revision": 2,
        "supersedes_id": "rec-hours-v1",
        "effective_until": datetime(2022, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    })

    fact = retriever.retrieve_authoritative_fact(
        query=query, account_key="primary", now=now
    )

    # Fail-closed: successor is expired; predecessor is superseded. Neither is authoritative.
    assert fact is None


# ---------------------------------------------------------------------------
# 7. Semantic Curator Candidate Discovery
# ---------------------------------------------------------------------------


def test_find_similar_knowledge_records(repo, emb_service):
    """find_similar_knowledge_records locates near-duplicates above threshold."""
    repo.save_record({
        "id": "rec-giftcard",
        "canonical_key": "gift-cards",
        "content": "Gift card purchases are completely non-refundable under any circumstances.",
        "authority_level": "canonical_knowledge",
        "retrieval_enabled": True,
        "status": "active",
    })
    repo.save_record({
        "id": "rec-parking",
        "canonical_key": "parking",
        "content": "Free customer parking is located behind the building.",
        "authority_level": "canonical_knowledge",
        "retrieval_enabled": True,
        "status": "active",
    })

    # Incoming draft similar to giftcard policy
    draft = "Gift cards cannot be refunded under any circumstance."
    matches = find_similar_knowledge_records(
        text=draft,
        repository=repo,
        threshold=0.5,
        limit=5,
        embedding_service=emb_service,
    )

    assert len(matches) >= 1
    top_record, score = matches[0]
    assert top_record.id == "rec-giftcard"
    assert score >= 0.5

    # Completely unrelated text yields no matches above standard threshold 0.75
    unrelated = "Quantum gravitational physics and warp drive engine specifications."
    no_matches = find_similar_knowledge_records(
        text=unrelated,
        repository=repo,
        threshold=0.75,
        limit=5,
        embedding_service=emb_service,
    )
    assert len(no_matches) == 0


# ---------------------------------------------------------------------------
# 8. Account & Tenant Eligibility Scoping
# ---------------------------------------------------------------------------


def test_account_isolation(repo, retriever):
    """Records scoped to secondary line are not returned when querying primary line."""
    repo.save_record({
        "id": "rec-secondary-only",
        "canonical_key": "vip-line-hours",
        "content": "VIP support line operates 24/7.",
        "authority_level": "owner_instruction",
        "retrieval_enabled": True,
        "status": "active",
        "account_key": "secondary",
    })

    query = "VIP support line hours"
    primary_fact = retriever.retrieve_authoritative_fact(query=query, account_key="primary")
    assert primary_fact is None

    secondary_fact = retriever.retrieve_authoritative_fact(query=query, account_key="secondary")
    assert secondary_fact is not None
    assert secondary_fact["id"] == "rec-secondary-only"


def test_inactive_and_retrieval_disabled_records_excluded(repo, retriever):
    """Quarantined or retrieval-disabled records must not be returned as authoritative facts."""
    repo.save_record({
        "id": "rec-quarantined",
        "canonical_key": "draft-policy",
        "content": "Draft policy on pet grooming services.",
        "authority_level": "canonical_knowledge",
        "retrieval_enabled": False,
        "status": "quarantined",
        "account_key": "primary",
    })

    fact = retriever.retrieve_authoritative_fact("pet grooming services", account_key="primary")
    assert fact is None

"""Comprehensive unit tests for Graduated Curator Autonomy & Policy Engine (Phase 6).

Covers:
- Level 0: All actions require human approval.
- Level 1: Exact duplicate suppression and invalid record quarantine can auto-execute;
           business fact modifications require approval.
- Level 2: Authorized owner answer without conflict is auto-approved into active knowledge
           with provenance and aliases.
- Level 2 Conflict Gate: Owner answer contradicting an existing active fact is blocked
                         from auto-approval and flagged for human review.
- Level 2 Dynamic Fact Gate: Owner answer containing literal price/time is blocked
                             from auto-approval and flagged for human review.
- Level 2 Actor Gate: Non-owner / customer assertion is blocked from auto-approval.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.curator.autonomy import (
    AutonomyDecision,
    AutonomyLevel,
    CuratorAutonomyPolicyEngine,
)
from backend.knowledge.models import (
    Base,
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeRecord,
)
from backend.knowledge.repository import KnowledgeRepository


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
    """KnowledgeRepository instance bound to the test session."""
    return KnowledgeRepository(db_session)


# ---------------------------------------------------------------------------
# Test 1: Level 0 - All actions require human approval
# ---------------------------------------------------------------------------

def test_level_0_all_actions_require_approval(repo):
    """Verify that in Level 0 (Proposal Only), all actions require human approval."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_0_PROPOSAL_ONLY)
    assert engine.get_level() == 0

    # Housekeeping actions require human approval in Level 0
    decision_dup = engine.evaluate_action("exact_duplicate_suppression")
    assert decision_dup.can_auto_execute is False
    assert decision_dup.requires_human_approval is True
    assert decision_dup.autonomy_level == 0
    assert "proposal_only_mode" in decision_dup.safety_flags

    decision_quar = engine.evaluate_action("invalid_record_quarantine")
    assert decision_quar.can_auto_execute is False
    assert decision_quar.requires_human_approval is True

    # Business fact modification requires human approval in Level 0
    decision_fact = engine.evaluate_action(
        "modify_business_fact",
        content="Free parking behind clinic",
        canonical_key="parking",
        actor_role="owner",
    )
    assert decision_fact.can_auto_execute is False
    assert decision_fact.requires_human_approval is True

    # Trusted owner answer requires human approval in Level 0
    decision_answer = engine.process_trusted_human_answer(
        question="Is parking available?",
        answer="Complimentary guest parking is available behind the clinic.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )
    assert decision_answer.can_auto_execute is False
    assert decision_answer.requires_human_approval is True
    assert "insufficient_autonomy_level" in decision_answer.safety_flags
    # Verify no record was written to the repository
    assert len(repo.list_active()) == 0


# ---------------------------------------------------------------------------
# Test 2: Level 1 - Housekeeping auto-executes, business facts require review
# ---------------------------------------------------------------------------

def test_level_1_housekeeping_vs_business_facts(repo):
    """Verify Level 1 allows non-destructive housekeeping but blocks business fact changes."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_1_HOUSEKEEPING)
    assert engine.get_level() == 1

    # Exact duplicate suppression auto-executes
    decision_dup = engine.evaluate_action("exact_duplicate_suppression")
    assert decision_dup.can_auto_execute is True
    assert decision_dup.requires_human_approval is False
    assert decision_dup.autonomy_level == 1
    assert decision_dup.safety_flags == []

    # Invalid record quarantine auto-executes
    decision_quar = engine.evaluate_action("invalid_record_quarantine")
    assert decision_quar.can_auto_execute is True
    assert decision_quar.requires_human_approval is False
    assert decision_quar.autonomy_level == 1

    # Alias attachment and question clustering auto-execute
    decision_alias = engine.evaluate_action("alias_attachment")
    assert decision_alias.can_auto_execute is True
    assert decision_alias.requires_human_approval is False

    decision_cluster = engine.evaluate_action("question_clustering")
    assert decision_cluster.can_auto_execute is True
    assert decision_cluster.requires_human_approval is False

    # Business fact modification requires human approval
    decision_mod = engine.evaluate_action(
        "modify_business_fact",
        content="Our cancellation fee is waived for emergencies.",
        canonical_key="cancellation-policy",
    )
    assert decision_mod.can_auto_execute is False
    assert decision_mod.requires_human_approval is True
    assert "business_fact_modification" in decision_mod.safety_flags

    # Trusted owner answer requires human approval at Level 1
    decision_answer = engine.process_trusted_human_answer(
        question="Is parking available?",
        answer="Complimentary guest parking is available behind the clinic.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )
    assert decision_answer.can_auto_execute is False
    assert decision_answer.requires_human_approval is True
    assert "insufficient_autonomy_level" in decision_answer.safety_flags
    assert len(repo.list_active()) == 0


# ---------------------------------------------------------------------------
# Test 3: Level 2 - Authorized owner answer auto-approved with provenance & aliases
# ---------------------------------------------------------------------------

def test_level_2_trusted_owner_answer_auto_approved(repo, db_session):
    """Verify authorized owner answer without conflict is auto-approved into active knowledge."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)
    assert engine.get_level() == 2

    question = "Where is visitor parking located?"
    answer = "Complimentary visitor parking is located in the dedicated lot behind the clinic."
    actor_id = "owner-42"
    actor_role = "owner"
    account_key = "primary"

    decision = engine.process_trusted_human_answer(
        question=question,
        answer=answer,
        actor_id=actor_id,
        actor_role=actor_role,
        account_key=account_key,
        repository=repo,
        confidence=1.0,
    )

    # Decision assertions
    assert decision.can_auto_execute is True
    assert decision.requires_human_approval is False
    assert decision.safety_flags == []
    assert decision.record_id is not None

    # KnowledgeRecord persistence assertions
    active_records = repo.list_active(account_key="primary")
    assert len(active_records) == 1
    rec = active_records[0]

    assert rec.id == decision.record_id
    assert rec.status == "active"
    assert rec.retrieval_enabled is True
    assert rec.authority_level == "owner_instruction"
    assert rec.source_type == "owner_answer"
    assert rec.source_actor_id == actor_id
    assert rec.content == answer
    assert rec.account_key == "primary"
    assert rec.confidence == 1.0

    # Provenance / KnowledgeEvidence audit assertions
    evidence_items = list(db_session.scalars(
        select(KnowledgeEvidence).where(KnowledgeEvidence.knowledge_id == rec.id)
    ).all())
    assert len(evidence_items) >= 1
    ev = evidence_items[0]
    assert ev.source_type == "owner_answer"
    assert ev.source_actor_id == actor_id
    assert ev.original_text_reference == question

    # KnowledgeAlias attachment assertions
    aliases = repo.get_aliases(rec.id)
    assert len(aliases) >= 1
    alias = aliases[0]
    assert alias.utterance == question
    assert alias.source == "owner_answer"


# ---------------------------------------------------------------------------
# Test 4: Level 2 Conflict Gate - Contradictory owner answer requires human review
# ---------------------------------------------------------------------------

def test_level_2_conflict_gate_blocks_auto_approval(repo):
    """Verify owner answer contradicting an existing active fact is blocked from auto-approval."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)

    # Pre-populate repository with an active record on visitor parking
    repo.save_record({
        "id": "rec-parking-001",
        "canonical_key": "where-is-visitor-parking-located",
        "content": "Visitor parking is street parking only.",
        "normalised_content": "visitor parking is street parking only.",
        "status": "active",
        "retrieval_enabled": True,
        "account_key": "primary",
        "authority_level": "canonical_knowledge",
    })

    # Owner attempts to submit a conflicting answer for the same topic
    decision = engine.process_trusted_human_answer(
        question="Where is visitor parking located?",
        answer="Complimentary visitor parking is available on site behind the clinic.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )

    assert decision.can_auto_execute is False
    assert decision.requires_human_approval is True
    assert "conflict_detected" in decision.safety_flags
    assert "active_rule_replacement" in decision.safety_flags
    assert decision.autonomy_level == AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED

    # Verify repository was not mutated: original record remains intact
    records = repo.list_active(account_key="primary")
    assert len(records) == 1
    assert records[0].id == "rec-parking-001"
    assert records[0].content == "Visitor parking is street parking only."


def test_level_2_evaluate_action_conflict_gate(repo):
    """Verify evaluate_action also gates on active record conflicts and replacements."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)

    repo.save_record({
        "id": "rec-cancellation-001",
        "canonical_key": "cancellation-fee",
        "content": "Standard cancellation fee is applied.",
        "normalised_content": "standard cancellation fee is applied.",
        "status": "active",
        "retrieval_enabled": True,
        "account_key": "primary",
    })

    # Action replacing existing rule triggers replacement gate
    decision_replace = engine.evaluate_action(
        "update_policy",
        canonical_key="cancellation-fee",
        content="No cancellation fees are charged ever.",
        actor_role="owner",
        repository=repo,
        replaces_existing=True,
    )
    assert decision_replace.can_auto_execute is False
    assert decision_replace.requires_human_approval is True
    assert "replacement_detected" in decision_replace.safety_flags

    # Action conflicting with existing record triggers conflict gate
    decision_conflict = engine.evaluate_action(
        "propose_rule",
        canonical_key="cancellation-fee",
        content="Different cancellation fee applies.",
        actor_role="owner",
        repository=repo,
    )
    assert decision_conflict.can_auto_execute is False
    assert decision_conflict.requires_human_approval is True
    assert "conflict_detected" in decision_conflict.safety_flags


# ---------------------------------------------------------------------------
# Test 5: Level 2 Dynamic Fact Gate - Literal price/time blocked
# ---------------------------------------------------------------------------

def test_level_2_dynamic_fact_gate_blocks_price_and_time(repo):
    """Verify owner answers containing literal prices or volatile times are blocked."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)

    # 1. Answer containing literal price ($150)
    decision_price = engine.process_trusted_human_answer(
        question="How much is a comprehensive consultation?",
        answer="The initial consultation fee is $150.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )
    assert decision_price.can_auto_execute is False
    assert decision_price.requires_human_approval is True
    assert "dynamic_fact_detected" in decision_price.safety_flags
    assert decision_price.autonomy_level == AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED

    # 2. Answer containing literal time and day (tomorrow at 10am)
    decision_time = engine.process_trusted_human_answer(
        question="When can I see the doctor next?",
        answer="We have an open appointment slot tomorrow at 10am.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )
    assert decision_time.can_auto_execute is False
    assert decision_time.requires_human_approval is True
    assert "dynamic_fact_detected" in decision_time.safety_flags

    # 3. Answer containing day of week literal (Monday)
    decision_day = engine.process_trusted_human_answer(
        question="Can I come in next week?",
        answer="Our next available appointment is Monday at 2pm.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )
    assert decision_day.can_auto_execute is False
    assert decision_day.requires_human_approval is True
    assert "dynamic_fact_detected" in decision_day.safety_flags

    # Verify no records were persisted
    assert len(repo.list_active()) == 0


# ---------------------------------------------------------------------------
# Test 6: Level 2 Actor Gate - Non-owner / customer assertion blocked
# ---------------------------------------------------------------------------

def test_level_2_actor_gate_blocks_non_owner(repo):
    """Verify non-owner / customer assertions cannot auto-promote into canonical knowledge."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)

    # Customer attempt
    decision_customer = engine.process_trusted_human_answer(
        question="Can I bring my pet to the appointment?",
        answer="Yes, all pets are welcome in the clinic.",
        actor_id="cust-8888",
        actor_role="customer",
        account_key="primary",
        repository=repo,
    )
    assert decision_customer.can_auto_execute is False
    assert decision_customer.requires_human_approval is True
    assert "unauthorized_actor" in decision_customer.safety_flags

    # Anonymous / staff attempt without owner privilege
    decision_staff = engine.process_trusted_human_answer(
        question="Can I bring my pet to the appointment?",
        answer="Yes, pets are welcome.",
        actor_id="staff-101",
        actor_role="staff",
        account_key="primary",
        repository=repo,
    )
    assert decision_staff.can_auto_execute is False
    assert decision_staff.requires_human_approval is True
    assert "unauthorized_actor" in decision_staff.safety_flags

    # Authorized admin attempt succeeds
    decision_admin = engine.process_trusted_human_answer(
        question="Can I bring my pet to the appointment?",
        answer="Service animals only are permitted inside the clinic facilities.",
        actor_id="admin-1",
        actor_role="admin",
        account_key="primary",
        repository=repo,
    )
    assert decision_admin.can_auto_execute is True
    assert decision_admin.requires_human_approval is False
    assert decision_admin.safety_flags == []
    assert len(repo.list_active()) == 1


# ---------------------------------------------------------------------------
# Test 7: Level 3 Mandatory Approval Gates & Edge Cases
# ---------------------------------------------------------------------------

def test_level_3_mandatory_human_approval_gates(repo):
    """Verify Level 3 mandates human approval for conflicts, replacements, cross-scope, dynamic facts."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED)
    assert engine.get_level() == 3

    # Cross-scope changes require approval
    decision_cross = engine.evaluate_action(
        "reconcile_scope",
        content="Shared operational rule",
        actor_role="owner",
        is_cross_scope=True,
    )
    assert decision_cross.can_auto_execute is False
    assert decision_cross.requires_human_approval is True
    assert "cross_scope_change" in decision_cross.safety_flags
    assert decision_cross.autonomy_level == 3

    # Dynamic facts require approval
    decision_dynamic = engine.evaluate_action(
        "add_fact",
        content="Consultation fee is $200",
        actor_role="owner",
    )
    assert decision_dynamic.can_auto_execute is False
    assert decision_dynamic.requires_human_approval is True
    assert "dynamic_fact_detected" in decision_dynamic.safety_flags

    # Low confidence (< 0.8) requires review
    decision_low_conf = engine.process_trusted_human_answer(
        question="Where is the elevator?",
        answer="The elevator is by the north lobby.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
        confidence=0.65,
    )
    assert decision_low_conf.can_auto_execute is False
    assert decision_low_conf.requires_human_approval is True
    assert "low_confidence" in decision_low_conf.safety_flags


def test_engine_level_transitions():
    """Verify engine level get/set and default handling."""
    engine = CuratorAutonomyPolicyEngine()
    assert engine.get_level() == AutonomyLevel.LEVEL_0_PROPOSAL_ONLY

    engine.set_level(AutonomyLevel.LEVEL_1_HOUSEKEEPING)
    assert engine.get_level() == 1

    engine.set_level(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)
    assert engine.get_level() == 2

    engine.set_level(AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED)
    assert engine.get_level() == 3

    # Invalid level defaults to LEVEL_0_PROPOSAL_ONLY
    engine.set_level(999)
    assert engine.get_level() == 0


def test_level_2_evaluate_action_unauthorized_actor_none():
    """Verify evaluate_action with actor_role=None or omitted at Level 2 is blocked with unauthorized_actor."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)

    decision = engine.evaluate_action(
        "modify_business_fact",
        content="Free consultations for seniors",
        canonical_key="senior-discount",
        actor_role=None,
    )
    assert decision.can_auto_execute is False
    assert decision.requires_human_approval is True
    assert "unauthorized_actor" in decision.safety_flags
    assert "Only 'owner' or 'admin'" in decision.reason


def test_level_2_process_trusted_human_answer_empty_answer_blocked(repo):
    """Verify process_trusted_human_answer with empty or whitespace-only answer is blocked with empty_answer."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)

    # Empty string
    decision_empty = engine.process_trusted_human_answer(
        question="What is the clinic WiFi password?",
        answer="",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )
    assert decision_empty.can_auto_execute is False
    assert decision_empty.requires_human_approval is True
    assert "empty_answer" in decision_empty.safety_flags
    assert decision_empty.reason == "Answer content cannot be empty."

    # Whitespace-only string
    decision_whitespace = engine.process_trusted_human_answer(
        question="What is the clinic WiFi password?",
        answer="   \n\t  ",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
    )
    assert decision_whitespace.can_auto_execute is False
    assert decision_whitespace.requires_human_approval is True
    assert "empty_answer" in decision_whitespace.safety_flags
    assert decision_whitespace.reason == "Answer content cannot be empty."
    assert len(repo.list_active()) == 0


def test_level_2_process_trusted_human_answer_custom_canonical_key(repo):
    """Verify process_trusted_human_answer uses custom canonical_key when provided."""
    engine = CuratorAutonomyPolicyEngine(AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS)

    decision = engine.process_trusted_human_answer(
        question="Where should patients wait?",
        answer="Patients should wait in the secondary lounge area.",
        actor_id="owner-1",
        actor_role="owner",
        account_key="primary",
        repository=repo,
        canonical_key="waiting-area-secondary",
    )
    assert decision.can_auto_execute is True
    assert decision.record_id is not None

    active_records = repo.list_active(account_key="primary")
    assert len(active_records) == 1
    assert active_records[0].canonical_key == "waiting-area-secondary"


"""Graduated Curator Autonomy and Policy Engine (Phase 6).

Enforces graduated autonomy policies:
- Level 0 (LEVEL_0_PROPOSAL_ONLY): Default proposal-only behavior. All actions require human approval.
- Level 1 (LEVEL_1_HOUSEKEEPING): Non-destructive autonomous housekeeping (exact duplicate
  suppression, alias attachment, question clustering, invalid record quarantining).
- Level 2 (LEVEL_2_TRUSTED_ANSWERS): Trusted human answers from authorized owners/admins become
  canonical knowledge automatically ONLY IF no conflict exists, not a volatile dynamic fact,
  scope is clear, and confidence is high.
- Level 3 (LEVEL_3_APPROVAL_REQUIRED): Mandatory human approval for all conflicts, replacements
  of existing active rules, cross-scope changes, and dynamic facts.
"""

from dataclasses import dataclass, field
from enum import IntEnum
import re
from typing import Any, Dict, List, Optional, Set

from backend.curator.authority import _canonical_knowledge_key
from backend.curator.sanitizer import (
    _curator_dynamic_claim_kind,
    has_unsafe_literal_learning_detail,
)
from backend.knowledge.models import KnowledgeRecord, utcnow
from backend.knowledge.repository import KnowledgeRepository


class AutonomyLevel(IntEnum):
    """Graduated autonomy levels for the Knowledge Curator."""

    LEVEL_0_PROPOSAL_ONLY = 0
    LEVEL_1_HOUSEKEEPING = 1
    LEVEL_2_TRUSTED_ANSWERS = 2
    LEVEL_3_APPROVAL_REQUIRED = 3


@dataclass
class AutonomyDecision:
    """Structured decision output from the Curator Autonomy Policy Engine."""

    can_auto_execute: bool
    autonomy_level: int
    action: str
    reason: str
    requires_human_approval: bool
    safety_flags: List[str] = field(default_factory=list)
    record_id: Optional[str] = None


# Non-destructive autonomous housekeeping actions allowed at Level 1+
HOUSEKEEPING_ACTIONS: Set[str] = {
    # Exact duplicate suppression
    "exact_duplicate_suppression",
    "exact_duplicate",
    "merge_duplicate",
    "suppress_duplicate",
    "duplicate_suppression",
    # Alias attachment
    "alias_attachment",
    "attach_alias",
    "add_alias",
    # Question clustering
    "question_clustering",
    "cluster_questions",
    "cluster_question",
    # Invalid record quarantining
    "invalid_record_quarantine",
    "invalid_record_quarantining",
    "quarantine_invalid_record",
    "quarantine_for_review",
    "quarantine",
}

AUTHORIZED_ROLES: Set[str] = {"owner", "admin"}
VALID_SCOPES: Set[str] = {"primary", "secondary", "shared"}
MIN_AUTONOMOUS_CONFIDENCE: float = 0.80


def _normalize_action_name(action: str) -> str:
    cleaned = str(action or "").strip().casefold()
    return re.sub(r"[\s\-]+", "_", cleaned)


class CuratorAutonomyPolicyEngine:
    """Policy engine determining whether curator mutations can auto-execute or require human approval."""

    def __init__(self, level: int = AutonomyLevel.LEVEL_0_PROPOSAL_ONLY) -> None:
        try:
            self.level: AutonomyLevel = AutonomyLevel(level)
        except (ValueError, TypeError):
            self.level = AutonomyLevel.LEVEL_0_PROPOSAL_ONLY

    def get_level(self) -> int:
        """Return the current autonomy level as an integer."""
        return int(self.level)

    def set_level(self, level: int) -> None:
        """Set the current autonomy level."""
        try:
            self.level = AutonomyLevel(level)
        except (ValueError, TypeError):
            self.level = AutonomyLevel.LEVEL_0_PROPOSAL_ONLY

    def is_authorized_actor(self, actor_role: Optional[str]) -> bool:
        """Check if actor role is authorized to perform privileged autonomous updates."""
        if not actor_role:
            return False
        return str(actor_role).strip().casefold() in AUTHORIZED_ROLES

    def evaluate_action(
        self,
        action: str,
        *,
        actor_role: Optional[str] = None,
        content: Optional[str] = None,
        canonical_key: Optional[str] = None,
        account_key: str = "primary",
        repository: Optional[KnowledgeRepository] = None,
        existing_records: Optional[List[Any]] = None,
        replaces_existing: bool = False,
        is_cross_scope: bool = False,
        confidence: float = 1.0,
        **kwargs: Any,
    ) -> AutonomyDecision:
        """Evaluate whether a curator action can auto-execute under current policy rules.

        Args:
            action: Identifier of the proposed action (e.g. 'exact_duplicate_suppression', 'modify_business_fact').
            actor_role: Role of the actor proposing the action ('owner', 'admin', 'customer', etc.).
            content: Candidate knowledge content or answer text.
            canonical_key: Canonical key/topic of the knowledge record.
            account_key: Scope/account ('primary', 'secondary', 'shared').
            repository: Optional KnowledgeRepository to inspect active records.
            existing_records: Optional pre-fetched records to check for collisions/conflicts.
            replaces_existing: True if the action replaces or modifies an active rule.
            is_cross_scope: True if the action modifies rules across accounts/scopes.
            confidence: Extraction or classification confidence score (0.0 to 1.0).

        Returns:
            AutonomyDecision with approval requirements and safety flags.
        """
        normalized_action = _normalize_action_name(action)

        # Rule 1: Level 0 preserves default proposal-only behavior for all actions
        if self.level == AutonomyLevel.LEVEL_0_PROPOSAL_ONLY:
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=int(self.level),
                action=action,
                reason="Autonomy Level 0 (Proposal Only): all curator actions require human approval.",
                requires_human_approval=True,
                safety_flags=["proposal_only_mode"],
            )

        is_housekeeping = normalized_action in HOUSEKEEPING_ACTIONS

        # Rule 2: Level 1 allows non-destructive autonomous housekeeping
        if self.level == AutonomyLevel.LEVEL_1_HOUSEKEEPING:
            if is_housekeeping:
                return AutonomyDecision(
                    can_auto_execute=True,
                    autonomy_level=int(self.level),
                    action=action,
                    reason="Autonomous housekeeping approved under Level 1 policy.",
                    requires_human_approval=False,
                    safety_flags=[],
                )
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=int(self.level),
                action=action,
                reason=f"Action '{action}' is a business fact modification requiring human approval at Level 1.",
                requires_human_approval=True,
                safety_flags=["business_fact_modification"],
            )

        # Level 2+: Housekeeping continues to auto-execute
        if is_housekeeping:
            return AutonomyDecision(
                can_auto_execute=True,
                autonomy_level=int(self.level),
                action=action,
                reason="Autonomous housekeeping approved.",
                requires_human_approval=False,
                safety_flags=[],
            )

        # Level 2+ Business Knowledge & Trusted Human Answers safety gates
        safety_flags: List[str] = []
        reasons: List[str] = []

        # Gate 1: Actor authorization
        if not self.is_authorized_actor(actor_role):
            safety_flags.append("unauthorized_actor")
            reasons.append(
                f"Actor role '{actor_role}' is not authorized. Only 'owner' or 'admin' can bypass human review."
            )

        # Gate 2: Dynamic fact safety
        text_to_check = content or kwargs.get("answer") or ""
        if text_to_check:
            dynamic_kind = _curator_dynamic_claim_kind(text_to_check)
            if dynamic_kind or has_unsafe_literal_learning_detail(text_to_check):
                flag = f"dynamic_fact_{dynamic_kind}" if dynamic_kind else "dynamic_fact_detected"
                safety_flags.append("dynamic_fact_detected")
                if flag != "dynamic_fact_detected":
                    safety_flags.append(flag)
                reasons.append(
                    f"Volatile dynamic fact detected ({dynamic_kind or 'literal detail'}). Dynamic facts require review."
                )

        # Gate 3: Scope clarity and cross-scope boundaries
        clean_scope = str(account_key or "").strip().casefold()
        if clean_scope not in VALID_SCOPES:
            safety_flags.append("ambiguous_scope")
            reasons.append(f"Scope '{account_key}' is not a recognized account key.")

        if is_cross_scope:
            safety_flags.append("cross_scope_change")
            reasons.append("Cross-scope changes require mandatory human review.")

        # Gate 4: Confidence threshold
        if confidence < MIN_AUTONOMOUS_CONFIDENCE:
            safety_flags.append("low_confidence")
            reasons.append(
                f"Confidence {confidence:.2f} is below autonomous threshold ({MIN_AUTONOMOUS_CONFIDENCE:.2f})."
            )

        # Gate 5: Existing active rule replacement
        if replaces_existing:
            safety_flags.append("replacement_detected")
            reasons.append("Replacing an existing active rule requires mandatory human approval.")

        # Gate 6: Conflict detection with existing active records
        resolved_key = canonical_key or kwargs.get("key") or kwargs.get("topic")
        if not resolved_key and text_to_check:
            resolved_key = _canonical_knowledge_key(text_to_check)

        records_to_scan: List[Any] = []
        if existing_records is not None:
            records_to_scan = existing_records
        elif repository is not None and resolved_key:
            try:
                records_to_scan = repository.list_active(account_key=clean_scope, include_shared=True)
            except Exception:
                records_to_scan = []

        if resolved_key and records_to_scan:
            norm_content = text_to_check.strip().casefold() if text_to_check else ""
            for rec in records_to_scan:
                rec_key = getattr(rec, "canonical_key", None)
                if rec_key is None and isinstance(rec, dict):
                    rec_key = rec.get("canonical_key")

                if rec_key == resolved_key:
                    rec_content = getattr(rec, "normalised_content", None) or getattr(rec, "content", None)
                    if rec_content is None and isinstance(rec, dict):
                        rec_content = rec.get("normalised_content") or rec.get("content")
                    rec_norm = str(rec_content or "").strip().casefold()

                    if norm_content and rec_norm and rec_norm != norm_content:
                        safety_flags.append("conflict_detected")
                        reasons.append(
                            f"Conflicting active record already exists for canonical key '{resolved_key}'."
                        )
                        break

        # Rule 4 & Decision synthesis
        if safety_flags:
            # Mandatory human approval for conflicts, replacements, cross-scope, dynamic facts
            is_level_3_trigger = any(
                f in safety_flags
                for f in ("conflict_detected", "replacement_detected", "dynamic_fact_detected", "cross_scope_change")
            )
            decision_level = AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED if is_level_3_trigger else int(self.level)
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=decision_level,
                action=action,
                reason="; ".join(reasons),
                requires_human_approval=True,
                safety_flags=safety_flags,
            )

        return AutonomyDecision(
            can_auto_execute=True,
            autonomy_level=int(self.level),
            action=action,
            reason="Action meets all safety and authorization gates for autonomous execution.",
            requires_human_approval=False,
            safety_flags=[],
        )

    def process_trusted_human_answer(
        self,
        question: str,
        answer: str,
        actor_id: str,
        actor_role: str,
        account_key: str,
        repository: KnowledgeRepository,
        confidence: float = 1.0,
        canonical_key: Optional[str] = None,
    ) -> AutonomyDecision:
        """Process a trusted human answer from an authorized actor.

        If level < LEVEL_2_TRUSTED_ANSWERS, requires review.
        If actor_role not in {"owner", "admin"}, requires review.
        If answer contains dynamic facts (prices, times, availability), flags safety issue and requires review.
        If active record collision/conflict/replacement detected, requires review.
        If all safe: auto-persists the new canonical KnowledgeRecord with status="active",
        retrieval_enabled=True, authority_level="owner_instruction" and attaches aliases.
        """
        clean_q = str(question or "").strip()
        clean_a = str(answer or "").strip()
        scope = str(account_key or "primary").strip().casefold()

        # Step 1: Check autonomy level
        if self.level < AutonomyLevel.LEVEL_2_TRUSTED_ANSWERS:
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=int(self.level),
                action="trusted_human_answer",
                reason=f"Current autonomy level ({int(self.level)}) does not permit autonomous answer ingestion. Requires Level 2 or higher.",
                requires_human_approval=True,
                safety_flags=["insufficient_autonomy_level"],
            )

        # Step 2: Check actor authorization
        if not self.is_authorized_actor(actor_role):
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=int(self.level),
                action="trusted_human_answer",
                reason=f"Actor role '{actor_role}' is not authorized. Only 'owner' or 'admin' can provide trusted answers.",
                requires_human_approval=True,
                safety_flags=["unauthorized_actor"],
            )

        # Step 2.5: Empty answer validation
        if not clean_a:
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=int(self.level),
                action="trusted_human_answer",
                reason="Answer content cannot be empty.",
                requires_human_approval=True,
                safety_flags=["empty_answer"],
            )

        # Step 3: Check dynamic fact safety
        dynamic_kind = _curator_dynamic_claim_kind(clean_a)
        if dynamic_kind or has_unsafe_literal_learning_detail(clean_a):
            flag = f"dynamic_fact_{dynamic_kind}" if dynamic_kind else "dynamic_fact_detected"
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED,
                action="trusted_human_answer",
                reason=f"Answer contains volatile dynamic facts ({dynamic_kind or 'literal details'}) requiring human review.",
                requires_human_approval=True,
                safety_flags=["dynamic_fact_detected", flag] if flag != "dynamic_fact_detected" else ["dynamic_fact_detected"],
            )

        # Step 4: Check scope and confidence
        if scope not in VALID_SCOPES:
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED,
                action="trusted_human_answer",
                reason=f"Account scope '{account_key}' is invalid or ambiguous.",
                requires_human_approval=True,
                safety_flags=["ambiguous_scope"],
            )

        if confidence < MIN_AUTONOMOUS_CONFIDENCE:
            return AutonomyDecision(
                can_auto_execute=False,
                autonomy_level=int(self.level),
                action="trusted_human_answer",
                reason=f"Confidence {confidence:.2f} is below autonomous threshold ({MIN_AUTONOMOUS_CONFIDENCE:.2f}).",
                requires_human_approval=True,
                safety_flags=["low_confidence"],
            )

        # Step 5: Check active authorities in repository for conflict or replacement
        resolved_key = str(canonical_key).strip() if canonical_key else ""
        if not resolved_key:
            resolved_key = _canonical_knowledge_key(clean_q)
        if not resolved_key:
            resolved_key = _canonical_knowledge_key(clean_a)
        if not resolved_key:
            resolved_key = "owner-guidance"
        canonical_key = resolved_key

        active_records = repository.list_active(account_key=scope, include_shared=True)
        matching_records = [r for r in active_records if r.canonical_key == canonical_key]

        if matching_records:
            norm_answer = clean_a.casefold()
            for r in matching_records:
                norm_existing = (r.normalised_content or r.content or "").strip().casefold()
                if norm_existing != norm_answer:
                    # Conflicting active fact or replacing active rule
                    return AutonomyDecision(
                        can_auto_execute=False,
                        autonomy_level=AutonomyLevel.LEVEL_3_APPROVAL_REQUIRED,
                        action="trusted_human_answer",
                        reason=f"Conflicting active record already exists for canonical key '{canonical_key}' (ID: {r.id}). Human approval required.",
                        requires_human_approval=True,
                        safety_flags=["conflict_detected", "active_rule_replacement"],
                    )

        # Step 6: All safe -> Auto-persist canonical KnowledgeRecord & attach aliases
        record_data = {
            "account_key": scope,
            "canonical_key": canonical_key,
            "knowledge_type": "policy",
            "content": clean_a,
            "normalised_content": clean_a.casefold(),
            "instruction": f"Answer question: {clean_q}" if clean_q else clean_a,
            "example_reply": clean_a,
            "status": "active",
            "retrieval_enabled": True,
            "authority_level": "owner_instruction",
            "source_type": "owner_answer",
            "source_actor_id": actor_id,
            "confidence": confidence,
            "verified_at": utcnow(),
        }
        evidence_data = {
            "source_type": "owner_answer",
            "source_actor_id": actor_id,
            "original_text_reference": clean_q or clean_a,
        }

        persisted_record = repository.save_record(record_data, evidence_data=evidence_data)

        # Attach utterance alias
        if clean_q:
            repository.add_alias(
                knowledge_id=persisted_record.id,
                utterance=clean_q,
                source="owner_answer",
            )

        return AutonomyDecision(
            can_auto_execute=True,
            autonomy_level=int(self.level),
            action="trusted_human_answer",
            reason=f"Trusted answer from authorized actor '{actor_id}' ({actor_role}) auto-approved into canonical knowledge.",
            requires_human_approval=False,
            safety_flags=[],
            record_id=persisted_record.id,
        )

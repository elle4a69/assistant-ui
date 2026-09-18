"""User-facing Business Assistant built on the existing chat and realtime transport.

This module intentionally has no source-code, GitHub, shell, deployment, infrastructure
or runtime-mutation tools. It owns onboarding, business knowledge refinement, curator
interviews, customer-conversation review and structured maintenance handoff.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

try:
    from backend.models.domain import OperationsAction
    from backend.services.operations_service import (
        OPERATIONS_TOOL_SCHEMAS,
        execute_operations_tool,
    )
    from backend.services.learning_service import (
        approve_learned_information_entry,
        generate_manual_learning,
        list_learned_information,
        save_manual_learning,
    )
    from backend.curator.service import (
        get_knowledge_curator_state,
        resolve_knowledge_curator_proposal,
        run_knowledge_curator,
    )
    from backend.curator.gaps import KnowledgeGapManager
    from backend.knowledge.models import Base as KnowledgeBase
    from backend.knowledge.repository import KnowledgeRepository
except ImportError:
    from models.domain import OperationsAction
    from services.operations_service import OPERATIONS_TOOL_SCHEMAS, execute_operations_tool
    from services.learning_service import (
        approve_learned_information_entry,
        generate_manual_learning,
        list_learned_information,
        save_manual_learning,
    )
    from curator.service import (
        get_knowledge_curator_state,
        resolve_knowledge_curator_proposal,
        run_knowledge_curator,
    )
    from curator.gaps import KnowledgeGapManager
    from knowledge.models import Base as KnowledgeBase
    from knowledge.repository import KnowledgeRepository


BUSINESS_ASSISTANT_SHARED_TOOL_NAMES = (
    "inspect_system_status",
    "inspect_recent_failures",
    "inspect_sms_accounts",
    "inspect_conversation",
    "list_unanswered_threads",
    "inspect_message_thread",
    "prepare_customer_sms_context",
    "send_sms",
    "save_sms_draft",
    "search_message_bodies",
    "diagnose_message_handling",
    "recall_operational_memory",
)

_BUSINESS_BASE_SCHEMAS = {
    item.get("name"): item for item in OPERATIONS_TOOL_SCHEMAS
    if item.get("name") in BUSINESS_ASSISTANT_SHARED_TOOL_NAMES
}

BUSINESS_ASSISTANT_TOOL_SCHEMAS = [
    *[_BUSINESS_BASE_SCHEMAS[name] for name in BUSINESS_ASSISTANT_SHARED_TOOL_NAMES if name in _BUSINESS_BASE_SCHEMAS],
    {
        "type": "function",
        "name": "list_curator_questions",
        "description": (
            "List the genuine unresolved business-knowledge questions that still need the owner's judgement. "
            "Use refresh=true when beginning a curator interview."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "account_key": {"type": "string", "enum": ["primary", "secondary", "shared"]},
                "include_history": {"type": "boolean"},
            },
            "required": ["refresh", "limit", "account_key", "include_history"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_curator_question",
        "description": "Retrieve one authoritative curator question and its auditable prior versions for its customer-service line.",
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {"type": "string", "minLength": 3, "maxLength": 255},
                "account_key": {"type": "string", "enum": ["primary", "secondary", "shared"]},
                "version": {"type": ["integer", "null"], "minimum": 1},
            },
            "required": ["question_id", "account_key", "version"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "edit_curator_question",
        "description": "Audit and edit the wording of an unresolved curator question after the owner confirms the wording.",
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {"type": "string"},
                "account_key": {"type": "string", "enum": ["primary", "secondary", "shared"]},
                "canonical_question": {"type": "string", "minLength": 2, "maxLength": 2000},
                "owner_question": {"type": "string", "minLength": 2, "maxLength": 2000},
                "confirmation_quote": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            "required": ["question_id", "account_key", "canonical_question", "owner_question", "confirmation_quote"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "answer_curator_question",
        "description": "Resolve a first-class curator question with the owner's confirmed reusable answer for that line.",
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {"type": "string"},
                "account_key": {"type": "string", "enum": ["primary", "secondary", "shared"]},
                "owner_answer": {"type": "string", "minLength": 2, "maxLength": 4000},
                "confirmation_quote": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            "required": ["question_id", "account_key", "owner_answer", "confirmation_quote"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "delete_curator_question",
        "description": "Soft-delete one curator question. Require the owner to type the returned exact phrase; the audit and undo remain available.",
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {"type": "string"},
                "account_key": {"type": "string", "enum": ["primary", "secondary", "shared"]},
                "confirmation_phrase": {"type": "string"},
            },
            "required": ["question_id", "account_key", "confirmation_phrase"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "undo_curator_question_change",
        "description": "Undo the latest audited curator-question change. Require the owner to type the exact undo phrase.",
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {"type": "string"},
                "account_key": {"type": "string", "enum": ["primary", "secondary", "shared"]},
                "confirmation_phrase": {"type": "string"},
            },
            "required": ["question_id", "account_key", "confirmation_phrase"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "draft_business_rule",
        "description": (
            "Turn the owner's requested refinement, correction or onboarding guidance into a safe pending business-rule "
            "draft. This never activates the rule. Read the returned interpretation back to the owner and ask for explicit confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "minLength": 2, "maxLength": 500},
                "guidance": {"type": "string", "minLength": 2, "maxLength": 4000},
                "scope": {"type": "string", "enum": ["shared", "primary", "secondary"]},
            },
            "required": ["topic", "guidance", "scope"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "confirm_business_rule",
        "description": (
            "Activate or safely restrict a previously drafted business rule only after the owner explicitly confirms the read-back. "
            "Pass a short verbatim confirmation such as 'yes', 'correct' or 'that's right'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "minLength": 3, "maxLength": 200},
                "confirmation_quote": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            "required": ["entry_id", "confirmation_quote"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "resolve_curator_question",
        "description": (
            "Close one curator question after the owner has explicitly confirmed the intended resolution. "
            "For a new or changed business rule, draft and confirm that rule first, then close the curator question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string", "minLength": 3, "maxLength": 200},
                "resolution": {
                    "type": "string",
                    "enum": [
                        "keep_all_examples", "select_current_rule", "create_merged_draft",
                        "needs_manual_investigation", "keep_both_distinct", "create_consolidation_draft",
                        "create_metadata_repair_draft", "add_safe_replacement_draft",
                        "not_an_issue", "dismiss_for_now"
                    ],
                },
                "selected_record_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
                "confirmation_quote": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            "required": ["proposal_id", "resolution", "selected_record_ids", "confirmation_quote"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "create_maintenance_handoff",
        "description": (
            "Create a structured maintenance handoff when the owner's problem appears technical. "
            "This records the issue for the separate maintenance/engineering agent and performs no coding or deployment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 3, "maxLength": 200},
                "observed_behavior": {"type": "string", "minLength": 3, "maxLength": 2000},
                "affected_area": {"type": "string", "minLength": 2, "maxLength": 200},
                "user_impact": {"type": "string", "minLength": 2, "maxLength": 1000},
                "evidence": {"type": "string", "maxLength": 2000},
            },
            "required": ["title", "observed_behavior", "affected_area", "user_impact", "evidence"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

BUSINESS_ASSISTANT_TOOLS = list(BUSINESS_ASSISTANT_TOOL_SCHEMAS)
BUSINESS_ASSISTANT_TOOL_NAMES = frozenset(item["name"] for item in BUSINESS_ASSISTANT_TOOLS)


def business_assistant_instructions(snapshot: str, memory: str = "[]", conversation: str = "") -> str:
    return (
        "You are the user's private Business Assistant for onboarding, ongoing business knowledge, curator interviews, "
        "customer-assistant behaviour refinement and general product help. The user never needs to know internal terms "
        "such as curator, proposal, knowledge record or scope unless they ask. Let them speak naturally, including "
        "frustrations such as 'line one is too flirtatious' or 'it keeps answering parking badly'. Inspect the relevant "
        "conversation or settings evidence when useful, identify the smallest durable business refinement and use "
        "draft_business_rule to prepare it. Always read the drafted meaning back in plain language and get an explicit "
        "confirmation before calling confirm_business_rule. For curator work, list each relevant account from the authoritative "
        "question store with history enabled, ask one clear question at a time, confirm your interpretation back to the user, "
        "then resolve it. Use the question retrieval tool before diagnosing a prior answer or revision. Edits are audited; deletion "
        "and undo require the exact separately typed confirmation phrase returned by the tool. Cluster the "
        "same underlying uncertainty instead of asking repetitive questions. During onboarding, gather useful missing "
        "business information conversationally rather than presenting a long form. At the end of a curator interview, "
        "invite the user to mention anything else they want changed, added or corrected. "
        "You may review customer conversations, help with messaging, save drafts and send an individual SMS when the "
        "owner explicitly asks. You have absolutely no coding, source-file, GitHub, shell, deployment, infrastructure or "
        "developer-setting capability. Never claim otherwise and never try to work around this boundary. If a reported "
        "problem appears technical, inspect only the safe evidence available to you, then call create_maintenance_handoff "
        "with a concise problem statement for the separate maintenance agent. Do not design code changes yourself. "
        "Dynamic facts such as current prices, durations, availability and booking times must come from their live "
        "authoritative settings/calendar rather than being memorised as literal rules. Keep primary, secondary and shared "
        "business knowledge separated correctly. Be warm, concise and practical, use Australian English and avoid "
        "unnecessary questions. "
        f"Current bounded system snapshot: {snapshot}\n"
        f"Durable business-assistant memory: {memory}\n"
        f"Recent persistent conversation: {conversation}"
    )


def _confirmation_present(value: str) -> bool:
    cleaned = " ".join(str(value or "").strip().casefold().split())
    if not cleaned:
        return False
    affirmative = {
        "yes", "yep", "yeah", "correct", "confirmed", "confirm", "that's right",
        "thats right", "that is right", "right", "okay", "ok", "proceed", "do it",
    }
    return cleaned in affirmative or cleaned.startswith("yes ") or cleaned.startswith("correct ")


def _gap_manager(db: Session) -> KnowledgeGapManager:
    KnowledgeBase.metadata.create_all(bind=db.get_bind())
    return KnowledgeGapManager(db)


def _present_gap_history(manager: KnowledgeGapManager, question_id: str, account_key: str) -> Dict[str, Any]:
    gap, revisions = manager.history(question_id, account_key)
    current_version = max((item.version for item in revisions), default=0) + 1
    return {
        "question": {**gap.to_public_dict(), "current_version": current_version, "can_undo": any(not item.undone for item in revisions)},
        "history": [item.to_public_dict() for item in revisions],
    }


def _list_curator_questions(db: Session, refresh: bool, limit: int, account_key: str, include_history: bool) -> Dict[str, Any]:
    if refresh:
        run_knowledge_curator()
    state = get_knowledge_curator_state()
    unresolved = [
        item for item in state.get("proposals", [])
        if item.get("status") in {"proposed", "accepted"}
    ]
    questions = []
    manager = _gap_manager(db)
    gaps = manager.list_gaps(account_key=account_key, tenant_id="default", limit=max(1, min(20, int(limit))))
    for gap in gaps:
        if gap.status == "deleted":
            continue
        item = gap.to_public_dict()
        item["source"] = "knowledge_gap"
        if include_history:
            item["history"] = _present_gap_history(manager, gap.id, account_key)["history"]
        questions.append(item)
        if len(questions) >= max(1, min(20, int(limit))):
            break
    for item in unresolved:
        if str(item.get("scope")) != account_key:
            continue
        owner_questions = [str(q).strip() for q in item.get("owner_questions", []) if str(q).strip()]
        if not owner_questions and item.get("proposed_action") != "ask_owner":
            continue
        previews = item.get("record_previews") if isinstance(item.get("record_previews"), list) else []
        questions.append({
            "proposal_id": item.get("id"),
            "source": "integrity_proposal",
            "finding_type": item.get("finding_type"),
            "scope": item.get("scope"),
            "question": owner_questions[0] if owner_questions else "This knowledge item needs a business decision.",
            "records": [
                {
                    "id": preview.get("id"),
                    "topic": preview.get("topic"),
                    "applies_when": preview.get("applies_when"),
                    "approved_reply": preview.get("approved_reply"),
                    "knowledge_text": preview.get("knowledge_text"),
                }
                for preview in previews[:4] if isinstance(preview, dict)
            ],
        })
        if len(questions) >= max(1, min(20, int(limit))):
            break
    proposal_history = []
    if include_history:
        proposal_history = [{
            "proposal_id": item.get("id"),
            "scope": item.get("scope"),
            "question": next((str(q).strip() for q in item.get("owner_questions", []) if str(q).strip()), None),
            "status": item.get("status"),
            "resolution": item.get("resolution"),
            "selected_record_ids": item.get("selected_record_ids", []),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        } for item in state.get("proposals", []) if str(item.get("scope")) == account_key][-20:]
    return {
        "status": "ok", "account_key": account_key, "count": len(questions),
        "questions": questions, "integrity_proposal_history": proposal_history,
    }


def execute_business_assistant_tool(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    current_user_message: str = "",
) -> Dict[str, Any]:
    if name not in BUSINESS_ASSISTANT_TOOL_NAMES:
        return {
            "status": "rejected",
            "reason": "That capability belongs to the separate maintenance/engineering agent.",
        }
    if name in BUSINESS_ASSISTANT_SHARED_TOOL_NAMES:
        return execute_operations_tool(db, name, arguments, current_user_message)
    if name == "list_curator_questions":
        return _list_curator_questions(
            db, bool(arguments.get("refresh")), int(arguments.get("limit", 10)),
            str(arguments.get("account_key") or "primary"), bool(arguments.get("include_history")),
        )
    if name == "get_curator_question":
        try:
            presented = _present_gap_history(_gap_manager(db), str(arguments.get("question_id") or ""), str(arguments.get("account_key") or ""))
            version = arguments.get("version")
            if version is None or int(version) == presented["question"]["current_version"]:
                return {"status": "ok", **presented}
            prior = next((item for item in presented["history"] if item["version"] == int(version)), None)
            return {"status": "ok", "version": prior} if prior else {"status": "rejected", "reason": "That version is unavailable for this account."}
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc)}
    if name == "edit_curator_question":
        if not _confirmation_present(str(arguments.get("confirmation_quote") or "")):
            return {"status": "rejected", "reason": "An explicit owner confirmation is required."}
        try:
            manager = _gap_manager(db)
            question_id = str(arguments.get("question_id") or "")
            account_key = str(arguments.get("account_key") or "")
            manager.update_gap(question_id, account_key, {
                "canonical_question": arguments.get("canonical_question"),
                "owner_question": arguments.get("owner_question"),
            }, actor_id="business-assistant")
            return {"status": "updated", **_present_gap_history(manager, question_id, account_key)}
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc)}
    if name == "answer_curator_question":
        if not _confirmation_present(str(arguments.get("confirmation_quote") or "")):
            return {"status": "rejected", "reason": "An explicit owner confirmation is required."}
        try:
            manager = _gap_manager(db)
            question_id = str(arguments.get("question_id") or "")
            account_key = str(arguments.get("account_key") or "")
            manager.history(question_id, account_key)  # Enforce line/account scope before resolution.
            record = manager.resolve_gap(
                question_id, str(arguments.get("owner_answer") or ""), KnowledgeRepository(db),
                author_id="business-assistant",
            )
            return {
                "status": "resolved", "question_id": question_id, "account_key": account_key,
                "knowledge_id": record.id, "revision": record.revision,
                **_present_gap_history(manager, question_id, account_key),
            }
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc)}
    if name in {"delete_curator_question", "undo_curator_question_change"}:
        question_id = str(arguments.get("question_id") or "")
        verb = "delete" if name == "delete_curator_question" else "undo"
        required = f"{verb} {question_id}"
        if str(arguments.get("confirmation_phrase") or "").strip() != required or str(current_user_message or "").strip() != required:
            return {"status": "pending_confirmation", "confirmation_phrase": required}
        try:
            manager = _gap_manager(db)
            account_key = str(arguments.get("account_key") or "")
            if verb == "delete":
                manager.delete_gap(question_id, account_key, actor_id="business-assistant")
            else:
                manager.undo_gap(question_id, account_key, actor_id="business-assistant")
            return {"status": "deleted" if verb == "delete" else "undone", **_present_gap_history(manager, question_id, account_key)}
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc)}
    if name == "draft_business_rule":
        scope = str(arguments.get("scope") or "shared")
        topic = str(arguments.get("topic") or "").strip()
        guidance = str(arguments.get("guidance") or "").strip()
        structured = generate_manual_learning(topic, guidance)
        entry = save_manual_learning(topic, guidance, structured, scope=scope)
        return {
            "status": "pending_confirmation",
            "entry_id": entry.get("id"),
            "scope": entry.get("scope") or scope,
            "interpretation": {
                "topic": structured.get("topic"),
                "applies_when": structured.get("applies_when"),
                "instruction": structured.get("instruction"),
                "example_reply": structured.get("example_reply"),
            },
            "instruction": "Read this interpretation back to the owner and ask for explicit confirmation before activation.",
        }
    if name == "confirm_business_rule":
        quote = str(arguments.get("confirmation_quote") or "")
        if not _confirmation_present(quote):
            return {"status": "rejected", "reason": "An explicit owner confirmation is required."}
        entry_id = str(arguments.get("entry_id") or "").strip()
        entries = {item.get("id"): item for item in list_learned_information()}
        entry = entries.get(entry_id)
        if not entry or entry.get("source_type") != "manual_guidance":
            return {"status": "rejected", "reason": "That pending business-rule draft is unavailable."}
        approved = approve_learned_information_entry(entry_id)
        return {
            "status": "confirmed",
            "entry_id": entry_id,
            "active_for_replies": bool(approved.get("retrieval_enabled")),
            "scope": approved.get("scope"),
            "review_status": approved.get("review_status"),
            "note": approved.get("review_note"),
        }
    if name == "resolve_curator_question":
        quote = str(arguments.get("confirmation_quote") or "")
        if not _confirmation_present(quote):
            return {"status": "rejected", "reason": "An explicit owner confirmation is required."}
        try:
            result = resolve_knowledge_curator_proposal(
                str(arguments.get("proposal_id") or ""),
                str(arguments.get("resolution") or ""),
                [str(v) for v in arguments.get("selected_record_ids", [])],
            )
            if result.get("draft_entry_id"):
                approved = approve_learned_information_entry(str(result["draft_entry_id"]))
                result = {
                    **result,
                    "draft_activated": bool(approved.get("retrieval_enabled")),
                    "draft_review_status": approved.get("review_status"),
                }
            return {"status": "resolved", "proposal": result}
        except (KeyError, ValueError) as exc:
            return {"status": "rejected", "reason": str(exc)}
    if name == "create_maintenance_handoff":
        payload = {
            "title": str(arguments.get("title") or "").strip()[:200],
            "observed_behavior": str(arguments.get("observed_behavior") or "").strip()[:2000],
            "affected_area": str(arguments.get("affected_area") or "").strip()[:200],
            "user_impact": str(arguments.get("user_impact") or "").strip()[:1000],
            "evidence": str(arguments.get("evidence") or "").strip()[:2000],
            "source": "business_assistant",
        }
        existing = (
            db.query(OperationsAction)
            .filter(
                OperationsAction.action_type == "maintenance_handoff",
                OperationsAction.status == "pending",
            )
            .order_by(OperationsAction.created_at.desc())
            .limit(20)
            .all()
        )
        for item in existing:
            try:
                prior = json.loads(item.payload or "{}")
            except (TypeError, json.JSONDecodeError):
                prior = {}
            if prior.get("title", "").casefold() == payload["title"].casefold():
                return {"status": "already_pending", "handoff_id": item.id, "title": payload["title"]}
        action = OperationsAction(
            action_type="maintenance_handoff",
            payload=json.dumps(payload, ensure_ascii=False),
            reason=payload["observed_behavior"][:1000],
            status="pending",
        )
        db.add(action)
        db.commit()
        db.refresh(action)
        return {
            "status": "handed_off",
            "handoff_id": action.id,
            "title": payload["title"],
            "note": "Recorded for the separate maintenance/engineering agent. No code or deployment action was taken.",
        }
    return {"status": "rejected", "reason": "Unsupported business-assistant tool."}


__all__ = [
    "BUSINESS_ASSISTANT_SHARED_TOOL_NAMES",
    "BUSINESS_ASSISTANT_TOOL_NAMES",
    "BUSINESS_ASSISTANT_TOOL_SCHEMAS",
    "BUSINESS_ASSISTANT_TOOLS",
    "business_assistant_instructions",
    "execute_business_assistant_tool",
]

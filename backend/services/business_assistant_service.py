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
    from backend.core.constants import OPERATIONS_CODE_SECRET_RE, OPERATIONS_MEMORY_PRIVATE_RE
    from backend.models.domain import OperationsAction, SupportTicket
    from backend.services.operations_service import (
        OPERATIONS_TOOL_SCHEMAS,
        _operations_start_coding_task,
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
except ImportError:
    from core.constants import OPERATIONS_CODE_SECRET_RE, OPERATIONS_MEMORY_PRIVATE_RE
    from models.domain import OperationsAction, SupportTicket
    from services.operations_service import OPERATIONS_TOOL_SCHEMAS, _operations_start_coding_task, execute_operations_tool
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


BUSINESS_ASSISTANT_SHARED_TOOL_NAMES = (
    "inspect_system_status",
    "inspect_recent_failures",
    "inspect_sms_accounts",
    "inspect_conversation",
    "list_unanswered_threads",
    "inspect_message_thread",
    "clear_thread_review_tags",
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
            },
            "required": ["refresh", "limit"],
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
            "Create a structured support ticket when a reported bug, feature request, upgrade or technical problem needs engineering. "
            "The ticket is anonymised, queued for the separate coding agent, and may prepare a review branch and deployment request; "
            "it can never deploy without the owner's later approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 3, "maxLength": 200},
                "category": {"type": "string", "enum": ["bug", "feature_request", "upgrade", "access", "security"]},
                "observed_behavior": {"type": "string", "minLength": 3, "maxLength": 2000},
                "affected_area": {"type": "string", "minLength": 2, "maxLength": 200},
                "user_impact": {"type": "string", "minLength": 2, "maxLength": 1000},
                "evidence": {"type": "string", "maxLength": 2000},
                "desired_outcome": {"type": "string", "minLength": 5, "maxLength": 1000},
            },
            "required": ["title", "category", "observed_behavior", "affected_area", "user_impact", "evidence", "desired_outcome"],
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
        "confirmation before calling confirm_business_rule. For curator work, refresh and list outstanding questions, "
        "ask one clear question at a time, confirm your interpretation back to the user, then resolve it. Cluster the "
        "same underlying uncertainty instead of asking repetitive questions. During onboarding, gather useful missing "
        "business information conversationally rather than presenting a long form. At the end of a curator interview, "
        "invite the user to mention anything else they want changed, added or corrected. "
        "You may review customer conversations, remove the Needs Review tag from up to 20 selected conversations, help "
        "with messaging, save drafts and send an individual SMS when the owner explicitly asks. Clearing review tags "
        "preserves drafts and never sends SMS. You have absolutely no coding, source-file, GitHub, shell, deployment, infrastructure or "
        "developer-setting capability. Never claim otherwise and never try to work around this boundary. If a reported "
        "problem appears technical, inspect only the safe evidence available to you, then call create_maintenance_handoff "
        "to create a concise support ticket for the separate maintenance agent. A ticket may be implemented and independently "
        "checked on a private review branch, but it can never deploy without the owner's later approval. Do not design code changes yourself. "
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


def _list_curator_questions(refresh: bool, limit: int) -> Dict[str, Any]:
    if refresh:
        run_knowledge_curator()
    state = get_knowledge_curator_state()
    unresolved = [
        item for item in state.get("proposals", [])
        if item.get("status") in {"proposed", "accepted"}
    ]
    questions = []
    for item in unresolved:
        owner_questions = [str(q).strip() for q in item.get("owner_questions", []) if str(q).strip()]
        if not owner_questions and item.get("proposed_action") != "ask_owner":
            continue
        previews = item.get("record_previews") if isinstance(item.get("record_previews"), list) else []
        questions.append({
            "proposal_id": item.get("id"),
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
    return {"status": "ok", "count": len(questions), "questions": questions}


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
        return _list_curator_questions(bool(arguments.get("refresh")), int(arguments.get("limit", 10)))
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
            "category": str(arguments.get("category") or "bug").strip(),
            "observed_behavior": str(arguments.get("observed_behavior") or "").strip()[:2000],
            "affected_area": str(arguments.get("affected_area") or "").strip()[:200],
            "user_impact": str(arguments.get("user_impact") or "").strip()[:1000],
            "evidence": str(arguments.get("evidence") or "").strip()[:2000],
            "desired_outcome": str(arguments.get("desired_outcome") or "").strip()[:1000],
            "source": "business_assistant",
        }
        if payload["category"] not in {"bug", "feature_request", "upgrade", "access", "security"}:
            return {"status": "rejected", "reason": "The support-ticket category is invalid."}
        if not all(payload[key] for key in ("title", "observed_behavior", "affected_area", "user_impact", "desired_outcome")):
            return {"status": "rejected", "reason": "The support ticket needs a title, impact, affected area and desired outcome."}
        if OPERATIONS_MEMORY_PRIVATE_RE.search("\n".join(str(value) for value in payload.values())) or OPERATIONS_CODE_SECRET_RE.search("\n".join(str(value) for value in payload.values())):
            return {"status": "rejected", "reason": "Remove customer contact details and secret values before creating an engineering ticket."}
        existing = (
            db.query(SupportTicket)
            .filter(
                SupportTicket.status.in_({"received", "engineering_queued", "engineering_in_progress", "awaiting_deployment"}),
            )
            .order_by(SupportTicket.created_at.desc())
            .limit(20)
            .all()
        )
        for item in existing:
            if item.title.casefold() == payload["title"].casefold():
                return {"status": "already_pending", "ticket_id": item.id, "title": payload["title"]}
        ticket = SupportTicket(
            source=payload["source"],
            category=payload["category"],
            title=payload["title"],
            observed_behavior=payload["observed_behavior"],
            affected_area=payload["affected_area"],
            user_impact=payload["user_impact"],
            evidence=payload["evidence"],
            status="received",
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        if ticket.category in {"access", "security"}:
            ticket.status = "awaiting_authorisation"
            ticket.resolution_summary = "This sensitive request needs explicit owner approval before any engineering work starts."
            db.commit()
            return {
                "status": "ticket_created",
                "ticket_id": ticket.id,
                "coding_task_id": None,
                "title": payload["title"],
                "ticket_status": ticket.status,
                "note": "Access and security changes are never started automatically; explicit owner authorisation is required.",
            }
        instructions = (
            "Investigate the following anonymised support ticket. Confirm the fault or requested behaviour from the codebase and tests, "
            "then implement the smallest safe end-to-end correction if the evidence supports it. Do not access customer records, secrets, "
            "or production data.\n\n"
            f"Ticket category: {payload['category']}\n"
            f"Observed behaviour: {payload['observed_behavior']}\n"
            f"Affected area: {payload['affected_area']}\n"
            f"User impact: {payload['user_impact']}\n"
            f"Desired outcome: {payload['desired_outcome']}\n"
            f"Evidence: {payload['evidence'] or 'No additional safe evidence was supplied.'}"
        )
        started = _operations_start_coding_task(
            db,
            ticket.title,
            instructions,
            payload["desired_outcome"],
            support_ticket_id=ticket.id,
            queue_if_busy=True,
            auto_propose_deployment=True,
        )
        if started.get("status") in {"started", "queued"}:
            ticket.coding_task_id = str(started.get("task_id") or "") or None
            ticket.status = "engineering_in_progress" if started["status"] == "started" else "engineering_queued"
            db.commit()
        else:
            ticket.status = "needs_review"
            ticket.resolution_summary = str(started.get("reason") or "The coding queue could not accept this ticket.")[:1000]
            db.commit()
        return {
            "status": "ticket_created",
            "ticket_id": ticket.id,
            "coding_task_id": ticket.coding_task_id,
            "title": payload["title"],
            "ticket_status": ticket.status,
            "note": (
                "The coding agent may prepare and independently check a private review branch. "
                "Production deployment always remains pending the owner's later approval."
            ),
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

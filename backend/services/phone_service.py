"""Phone thread management, information request, and catch-up service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from fastapi import HTTPException
from sqlalchemy import func

try:
    from backend.core.config import (
        BASE_DIR, DATA_DIR, FIRST_CONTACT_AUTORESPONDER_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, CONVERSATIONAL_AI_ACCOUNT_KEYS,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        INTERNAL_INSTRUCTION_REPLY_PATTERNS, UNSAFE_HOLDING_REPLY_PATTERNS,
        SERVICE_AND_BOOKING_CONVERSATION_POLICY, RELEVANCE_AND_THREAD_FLOW_POLICY,
        SMS_TYPOGRAPHY_POLICY, PROMPTS_DIR, STYLE_PROFILE_STORE,
    )
    from backend.core.constants import TAKEOVER_RELEASE_EVENT_TYPES
    from backend.core.state import (
        SMS_REPLY_THREAD_LOCKS, SMS_REPLY_GLOBAL_LOCK, get_thread_lock,
    )
    from backend.core.clients import (
        openai_client, mobilemessage_service, canonical_phone_number,
    )
    from backend.core.utils import normalized_reply_fingerprint, format_dt, sanitize_outgoing_urls, _dyn
    from backend.models.domain import Thread, Message, ThreadEvent
    from backend.services.settings_service import (
        account_allows_conversational_ai, load_first_contact_autoresponder,
        load_first_contact_autoresponders, load_business_variables,
        get_business_variable_values, get_line_business_variable_values,
        load_message_ui_settings,
    )
    from backend.services.booking_service import (
        current_business_time, build_read_only_calendar_context,
    )
    from backend.services.sms_service import (
        human_replied_after, is_latest_customer_turn, build_model_input,
        build_model_instructions, is_contact_blocked,
    )
    from backend.knowledge.style_retrieval import get_style_examples
    from backend.knowledge import render_template_variables
except ImportError:
    from core.config import (
        BASE_DIR, DATA_DIR, FIRST_CONTACT_AUTORESPONDER_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, CONVERSATIONAL_AI_ACCOUNT_KEYS,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        INTERNAL_INSTRUCTION_REPLY_PATTERNS, UNSAFE_HOLDING_REPLY_PATTERNS,
        SERVICE_AND_BOOKING_CONVERSATION_POLICY, RELEVANCE_AND_THREAD_FLOW_POLICY,
        SMS_TYPOGRAPHY_POLICY, PROMPTS_DIR, STYLE_PROFILE_STORE,
    )
    from core.constants import TAKEOVER_RELEASE_EVENT_TYPES
    from core.state import (
        SMS_REPLY_THREAD_LOCKS, SMS_REPLY_GLOBAL_LOCK, get_thread_lock,
    )
    from core.clients import (
        openai_client, mobilemessage_service, canonical_phone_number,
    )
    from core.utils import normalized_reply_fingerprint, format_dt, sanitize_outgoing_urls, _dyn
    from models.domain import Thread, Message, ThreadEvent
    from services.settings_service import (
        account_allows_conversational_ai, load_first_contact_autoresponder,
        load_first_contact_autoresponders, load_business_variables,
        get_business_variable_values, get_line_business_variable_values,
        load_message_ui_settings,
    )
    from services.booking_service import (
        current_business_time, build_read_only_calendar_context,
    )
    from services.sms_service import (
        human_replied_after, is_latest_customer_turn, build_model_input,
        build_model_instructions, is_contact_blocked,
    )
    from knowledge.style_retrieval import get_style_examples
    from knowledge import render_template_variables

logger = logging.getLogger(__name__)


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback

def generate_information_request_content(
    db: Session,
    thread: Thread,
    customer_message: Message,
    supplied_information: str,
) -> Dict[str, str]:
    """Turn owner-supplied facts into reusable knowledge and a customer reply."""
    if not openai_client:
        raise HTTPException(status_code=503, detail="The AI is unavailable, so no reply was sent.")

    system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
    system_prompt = "You are a friendly, natural customer service agent."
    if os.path.exists(system_prompt_path):
        with open(system_prompt_path, "r", encoding="utf-8") as handle:
            system_prompt = handle.read()

    instructions = build_model_instructions(
        render_template_variables(system_prompt, {
            **get_line_business_variable_values(thread.sms_account_key),
            "current_time": current_business_time().strftime("%A %d %B %Y, %I:%M %p %Z"),
        }),
        get_style_examples(customer_message.text, account_key=thread.sms_account_key),
        STYLE_PROFILE_STORE.get_applied(),
    )
    instructions += (
        "\n\nThe business owner has supplied the missing information below. Treat it as "
        "authoritative business information, not as a customer message. Produce a natural, concise "
        "reply to the customer's unanswered message. Do not mention internal checks, handoffs, the "
        "knowledge base, or that a human supplied the information. Do not use em dashes. Do not add "
        "facts that were not supplied. Also create a concise reusable knowledge summary. Remove "
        "customer identifiers and do not turn one-off dates, current availability, or private details "
        "into permanent business rules. Return only valid JSON with exactly these string fields: "
        '"customer_reply" and "knowledge_summary".'
    )
    prompt = (
        f"Customer's unanswered message:\n{customer_message.text}\n\n"
        f"Information supplied by the business owner:\n{supplied_information}"
    )
    response = openai_client.responses.create(
        model="gpt-5.6-terra",
        instructions=instructions,
        input=build_model_input(
            db.query(Message).filter(Message.thread_id == thread.id).order_by(
                Message.at.asc(), Message.id.asc()
            ).all(),
            current_history_text=customer_message.text,
            enriched_current_prompt=prompt,
        ),
        store=False,
    )
    raw_output = (response.output_text or "").strip()
    result: Dict[str, Any] = {}
    try:
        result = _parse_json_object(raw_output)
    except Exception:
        # Models occasionally wrap otherwise valid JSON in a short sentence or
        # markdown fence.  Recover the JSON object before treating the reply as
        # plain text; a formatting quirk must not discard owner-supplied facts.
        start, end = raw_output.find("{"), raw_output.rfind("}")
        if start >= 0 and end > start:
            try:
                recovered = json.loads(raw_output[start:end + 1])
                if isinstance(recovered, dict):
                    result = recovered
            except json.JSONDecodeError:
                pass

    customer_reply = sanitize_outgoing_urls(str(result.get("customer_reply", "")).strip())
    knowledge_summary = str(result.get("knowledge_summary", "")).strip()
    if not customer_reply:
        reply_match = re.search(r'"customer_reply"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_output, re.DOTALL)
        if reply_match:
            try:
                customer_reply = sanitize_outgoing_urls(json.loads(f'"{reply_match.group(1)}"').strip())
            except (json.JSONDecodeError, AttributeError):
                pass
    if not customer_reply and not raw_output.lstrip().startswith("{"):
        # A model that disregards the JSON shape still usually returns the
        # customer-ready SMS.  Use it after removing only presentation fences.
        customer_reply = sanitize_outgoing_urls(
            re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_output, flags=re.IGNORECASE).strip()
        )
    if not knowledge_summary:
        # The owner is the authority for this interaction.  The curator applies
        # the normal safety gate before this fallback can affect future replies.
        knowledge_summary = supplied_information.strip()
    if not customer_reply:
        raise HTTPException(status_code=502, detail="The AI could not prepare a customer reply. Nothing was sent.")
    return {"customer_reply": customer_reply, "knowledge_summary": knowledge_summary}


def find_pending_information_request(
    db: Session,
    thread_id: str,
    request_event_id: Optional[str] = None,
) -> Optional[ThreadEvent]:
    query = db.query(ThreadEvent).filter(
        ThreadEvent.thread_id == thread_id,
        ThreadEvent.type.in_(["information-request", "catch-up-handoff"]),
    )
    if request_event_id:
        query = query.filter(ThreadEvent.id == request_event_id)
    for event_item in query.order_by(ThreadEvent.at.desc()).all():
        try:
            event_meta = json.loads(event_item.meta or "{}")
        except (TypeError, json.JSONDecodeError):
            event_meta = {}
        if event_meta.get("status") != "resolved":
            return event_item
    return None


def has_active_explicit_takeover(db: Session, thread_id: str) -> bool:
    latest_control = db.query(ThreadEvent).filter(
        ThreadEvent.thread_id == thread_id,
        ThreadEvent.type.in_(["takeover", *TAKEOVER_RELEASE_EVENT_TYPES]),
    ).order_by(ThreadEvent.at.desc(), ThreadEvent.id.desc()).first()
    return bool(latest_control and latest_control.type == "takeover")


def list_catch_up_candidates(db: Session) -> List[tuple[Thread, Message]]:
    """Return unanswered conversations inside the configured catch-up window.

    An outstanding conversation is one whose newest customer message has not
    received a sent agent or AI reply. Drafts and stale workflow labels are not
    replies and must not hide the conversation from manual Catch-up.
    """
    ranked_customer_messages = db.query(
        Message.id.label("message_id"),
        Message.thread_id.label("thread_id"),
        func.row_number().over(
            partition_by=Message.thread_id,
            order_by=(Message.at.desc(), Message.id.desc()),
        ).label("row_number"),
    ).filter(Message.role == "customer").subquery()
    rows = db.query(Thread, Message).join(
        ranked_customer_messages,
        ranked_customer_messages.c.thread_id == Thread.id,
    ).join(
        Message,
        Message.id == ranked_customer_messages.c.message_id,
    ).filter(
        ranked_customer_messages.c.row_number == 1,
        Thread.auto_reply_enabled.is_(True),
    ).all()
    if not rows:
        return []

    thread_ids = [thread.id for thread, _message in rows]
    sent_reply_times = {
        thread_id: sent_at
        for thread_id, sent_at in db.query(Message.thread_id, func.max(Message.at)).filter(
            Message.thread_id.in_(thread_ids),
            Message.role.in_(["agent", "system"]),
        ).group_by(Message.thread_id).all()
    }
    events = db.query(ThreadEvent).filter(
        ThreadEvent.thread_id.in_(thread_ids),
        ThreadEvent.type.in_([
            "takeover",
            *TAKEOVER_RELEASE_EVENT_TYPES,
        ]),
    ).all()
    latest_control_events: Dict[str, ThreadEvent] = {}
    for event_item in events:
        if event_item.type == "takeover" or event_item.type in TAKEOVER_RELEASE_EVENT_TYPES:
            current = latest_control_events.get(event_item.thread_id)
            if current is None or (event_item.at, event_item.id) > (current.at, current.id):
                latest_control_events[event_item.thread_id] = event_item

    settings_fn = _dyn("load_message_ui_settings", load_message_ui_settings)
    catch_up_after = datetime.utcnow() - timedelta(
        days=settings_fn()["catchUpLookbackDays"]
    )
    candidates = []
    account_ai_fn = _dyn(
        "account_allows_conversational_ai",
        lambda account_key: account_key == "primary",
    )
    for thread, latest in rows:
        if is_contact_blocked(db, thread.sms_account_key, thread.customer_phone):
            continue
        # Do not retry a conversation for which the AI has specifically asked
        # an operator for information. That is an intentional human hold, not
        # an unanswered conversation the automatic responder can safely solve.
        if thread.state == "needs-review" and find_pending_information_request(db, thread.id):
            continue
        # The manual catch-up loop must respect the same per-line responder
        # control as live inbound processing. Otherwise a disabled line is
        # repeatedly selected, cannot send, and prevents later eligible lines
        # from being reached.
        if not account_ai_fn(thread.sms_account_key):
            continue
        # Do not turn historical inbound messages into fresh catch-up work.
        if latest.at < catch_up_after:
            continue
        # A draft is not a sent response. Only a real outbound agent or AI
        # message closes the customer's latest turn.
        if sent_reply_times.get(thread.id) and sent_reply_times[thread.id] >= latest.at:
            continue
        # A taken-over state is genuine only when an operator explicitly used
        # Take over. Draft approval/discard/cleanup historically set the same
        # state automatically and must not strand later customer messages.
        latest_control = latest_control_events.get(thread.id)
        if thread.state == "taken-over" and latest_control and latest_control.type == "takeover":
            continue
        candidates.append((thread, latest))
    return sorted(candidates, key=lambda item: (item[1].at, item[1].id))


def find_oldest_catch_up_candidate(db: Session):
    """Return the oldest conversation whose latest message is still unanswered."""
    candidates = list_catch_up_candidates(db)
    return candidates[0] if candidates else None


def _normalise_manual_reply_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _manual_reply_response(message: Message, duplicate: bool = False) -> Dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "text": message.text,
        "at": format_dt(message.at),
        "duplicate": duplicate,
    }


__all__ = [
    "generate_information_request_content",
    "find_pending_information_request",
    "has_active_explicit_takeover",
    "list_catch_up_candidates",
    "find_oldest_catch_up_candidate",
    "_normalise_manual_reply_text",
    "_manual_reply_response",
]

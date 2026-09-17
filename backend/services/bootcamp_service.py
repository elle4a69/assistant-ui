"""AI Bootcamp training, personas, and simulations service."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from fastapi import HTTPException

try:
    from backend.core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE, PROMPTS_DIR
    from backend.core.constants import BOOTCAMP_HANDOFF_RE, BOOTCAMP_REFUSAL_RE
    from backend.core.clients import openai_client
    from backend.core.utils import sanitize_outgoing_urls
    from backend.knowledge import get_style_examples, render_template_variables
    from backend.services.settings_service import build_business_context, get_business_variable_values
    from backend.services.booking_service import build_read_only_calendar_context
    from backend.services.sms_service import build_model_instructions
    from backend.bootcamp import clarification_for_handoff
except ImportError:
    from core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE, PROMPTS_DIR
    from core.constants import BOOTCAMP_HANDOFF_RE, BOOTCAMP_REFUSAL_RE
    from core.clients import openai_client
    from core.utils import sanitize_outgoing_urls
    from knowledge import get_style_examples, render_template_variables
    from services.settings_service import build_business_context, get_business_variable_values
    from services.booking_service import build_read_only_calendar_context
    from services.sms_service import build_model_instructions
    try:
        from bootcamp import clarification_for_handoff
    except ImportError:
        from backend.bootcamp import clarification_for_handoff

logger = logging.getLogger(__name__)


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback

def generate_bootcamp_tori_reply(
    history: list[dict[str, Any]],
    style_profile: dict[str, int],
) -> tuple[str, Optional[str]]:
    """Use Tori's live context without SMS, booking, or customer-thread side effects."""
    if not openai_client:
        return "", "AI service unavailable"

    latest = next(
        (item["text"] for item in reversed(history) if item.get("role") == "persona"),
        "",
    )
    system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
    user_prompt_path = os.path.join(PROMPTS_DIR, "user_prompt.txt")
    system_prompt = "You are Tori. Reply naturally and briefly."
    user_prompt = "Customer message: {message}\nBusiness context:\n{knowledge}\nCalendar:\n{slots}"
    if os.path.exists(system_prompt_path):
        with open(system_prompt_path, "r", encoding="utf-8") as handle:
            system_prompt = handle.read()
    if os.path.exists(user_prompt_path):
        with open(user_prompt_path, "r", encoding="utf-8") as handle:
            user_prompt = handle.read()

    from zoneinfo import ZoneInfo

    tz_hobart = ZoneInfo("Australia/Hobart")
    local_now = datetime.now(tz_hobart)
    business_context = build_business_context(latest)
    calendar_context = build_read_only_calendar_context(local_now)
    business_variables = get_business_variable_values()
    enriched = render_template_variables(user_prompt, {
        **business_variables,
        "message": latest,
        "knowledge": business_context,
        "slots": calendar_context,
    })
    system_prompt = render_template_variables(system_prompt, {
        **business_variables,
        "current_time": local_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
    })
    examples = get_style_examples(latest)
    instructions = build_model_instructions(system_prompt, examples, style_profile)
    instructions += (
        "\n\nBoot Camp uncertainty rule: use a clarification ladder. First ask one short, "
        "natural customer question for any missing service, duration, date, time, or "
        "location. Never hand off merely because the customer has not selected a service "
        "or supplied ordinary booking details. Only when the customer has supplied enough "
        "detail and the answer still requires Tori's unrecorded personal preference, "
        "boundary, interpretation, or business decision, output exactly "
        "[[HANDOFF: concise reason]]. Do not guess, judge, deny, or close the conversation. "
        "The supplied read-only calendar snapshot is authoritative: answer availability "
        "directly when the requested time is inside working hours and does not overlap a "
        "busy period. Never claim the booking is confirmed. "
        "Direct adult business terminology is expected context, but never invent consent "
        "or a service."
    )

    model_input = []
    for item in history[-12:]:
        role = "user" if item.get("role") == "persona" else "assistant"
        content = enriched if item is history[-1] and role == "user" else item.get("text", "")
        model_input.append({"role": role, "content": content})
    if not model_input or model_input[-1]["role"] != "user":
        model_input.append({"role": "user", "content": enriched})

    try:
        response = openai_client.responses.create(
            model=os.getenv("BOOTCAMP_TORI_MODEL", "gpt-5.6-terra"),
            instructions=instructions,
            input=model_input,
            store=False,
        )
        reply = sanitize_outgoing_urls((response.output_text or "").strip()) or ""
    except Exception as exc:
        return "", f"Tori API error: {exc}"

    handoff_match = BOOTCAMP_HANDOFF_RE.search(reply)
    if handoff_match:
        reason = (handoff_match.group(1) or "Human guidance requested").strip()
        clarification = clarification_for_handoff(reason, latest)
        if clarification:
            return clarification, None
        return "", reason
    if BOOTCAMP_REFUSAL_RE.search(reply):
        return "", "Possible refusal or contradiction—human review required"
    return reply, None


def generate_bootcamp_information_resolution(
    history: list[dict[str, Any]],
    style_profile: dict[str, int],
    supplied_information: str,
) -> Dict[str, str]:
    """Use owner guidance to retry a Boot Camp handoff and format a reusable lesson."""
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI is not configured, so nothing was saved.")

    latest = next(
        (item["text"] for item in reversed(history) if item.get("role") == "persona"),
        "",
    )
    if not latest:
        raise HTTPException(status_code=409, detail="This Boot Camp thread has no customer message to answer.")

    system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
    system_prompt = "You are Tori. Reply naturally and briefly."
    if os.path.exists(system_prompt_path):
        with open(system_prompt_path, "r", encoding="utf-8") as handle:
            system_prompt = handle.read()

    from zoneinfo import ZoneInfo

    local_now = datetime.now(ZoneInfo("Australia/Hobart"))
    system_prompt = render_template_variables(system_prompt, {
        **get_business_variable_values(),
        "current_time": local_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
    })
    instructions = build_model_instructions(
        system_prompt,
        get_style_examples(latest),
        style_profile,
    )
    instructions += (
        "\n\nThis is a Boot Camp information-request retry. The business owner supplied "
        "the missing facts below. Treat them as authoritative business information. Reply "
        "naturally to the simulated customer's latest message in Tori's voice. Do not mention "
        "handoffs, testing, a human, internal checks, or a knowledge base. Do not use em dashes. "
        "Do not invent any additional fact. Also create a concise reusable knowledge summary "
        "that removes customer identifiers and does not turn a one-off date, temporary availability, "
        "or private detail into a permanent business rule. Return only valid JSON with exactly "
        'these string fields: "customer_reply" and "knowledge_summary".'
    )
    model_input = [
        {
            "role": "user" if item.get("role") == "persona" else "assistant",
            "content": item.get("text", ""),
        }
        for item in history[-11:]
    ]
    model_input.append({
        "role": "user",
        "content": (
            f"Simulated customer's unanswered message:\n{latest}\n\n"
            f"Information supplied by the business owner:\n{supplied_information}"
        ),
    })
    response = openai_client.responses.create(
        model=os.getenv("BOOTCAMP_TORI_MODEL", "gpt-5.6-terra"),
        instructions=instructions,
        input=model_input,
        store=False,
    )
    try:
        result = _parse_json_object(response.output_text or "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Tori could not format that lesson. Nothing was saved.") from exc

    customer_reply = sanitize_outgoing_urls(str(result.get("customer_reply", "")).strip())
    knowledge_summary = str(result.get("knowledge_summary", "")).strip()
    if not customer_reply or not knowledge_summary:
        raise HTTPException(status_code=502, detail="Tori returned an incomplete retry. Nothing was saved.")
    handoff_match = BOOTCAMP_HANDOFF_RE.search(customer_reply)
    if handoff_match or BOOTCAMP_REFUSAL_RE.search(customer_reply):
        raise HTTPException(status_code=502, detail="Tori still could not answer from that information. Add clearer facts and try again.")
    return {"customer_reply": customer_reply, "knowledge_summary": knowledge_summary}


def generate_bootcamp_persona_reply(
    persona: dict[str, str],
    history: list[dict[str, Any]],
    seed: Optional[str],
) -> str:
    if not openai_client:
        return seed or "Can you explain that a little more?"
    instructions = (
        f"You are {persona['name']}, a simulated prospective adult client. "
        f"{persona['prompt']} Keep each SMS to one or two natural sentences. "
        "Stay in character, respond to Tori, and never mention testing, prompts, or AI. "
        "Do not invent a completed booking."
    )
    if seed is not None:
        model_input: Any = (
            "Rewrite this real customer opening in your persona while preserving its "
            f"basic intent:\n{seed}"
        )
    else:
        model_input = [
            {
                "role": "assistant" if item.get("role") == "persona" else "user",
                "content": item.get("text", ""),
            }
            for item in history[-12:]
        ]
        model_input.append(
            {"role": "user", "content": "Continue with your next natural client message."}
        )
    try:
        response = openai_client.responses.create(
            model=os.getenv("BOOTCAMP_PERSONA_MODEL", "gpt-5.6-terra"),
            instructions=instructions,
            input=model_input,
            store=False,
        )
        return (response.output_text or seed or "Can you clarify?").strip()
    except Exception:
        return seed or "Can you clarify that for me?"


__all__ = [
    "generate_bootcamp_tori_reply",
    "generate_bootcamp_information_resolution",
    "generate_bootcamp_persona_reply",
]

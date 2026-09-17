"""Sanitizer for reusable knowledge templates and volatile learning details."""

import re
from typing import Any, Dict, List, Optional

from .compat import get_main_attr

RESERVED_TEMPLATE_VARIABLES = {
    "message", "knowledge", "slots", "current_time", "name", "service", "time"
}
TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
SHARED_LITERAL_DATE_OR_TIME_PATTERN = re.compile(
    r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    re.IGNORECASE,
)

# Reusable knowledge is deliberately a tiny, data-only template language. It
# is not Python, Jinja, an expression language, or a way to reach Settings.
APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES = frozenset({
    "customer_first_name", "provider_name", "service_name", "service_price",
    "service_duration", "requested_date", "requested_time", "available_slots",
    "booking_reference", "provider_location", "information_url",
})


def _get_account_keys():
    return get_main_attr("FIRST_CONTACT_ACCOUNT_KEYS", ("primary", "secondary"))


def _get_line_profile(account_key: str):
    fn = get_main_attr("get_line_profile")
    if callable(fn):
        return fn(account_key)
    return {}


def _load_line_services(account_key: str):
    fn = get_main_attr("load_line_services")
    if callable(fn):
        return fn(account_key)
    return []


def _resolve_provider_context(account_key: str):
    fn = get_main_attr("resolve_provider_context")
    if callable(fn):
        return fn(account_key)
    return {"provider_name": "", "information_url": ""}


def _curator_dynamic_claim_detail(text: str) -> Optional[Dict[str, str]]:
    """Find an actual volatile assertion, never an availability discussion.

    This deliberately excludes template variables and instructions such as
    "check live availability". It is shared with the durable-learning gate.
    """
    value = re.sub(r"\{\{?[^{}]+\}?\}", "", str(text or ""))
    patterns = (
        ("price", r"\$\s*\d+(?:\.\d{1,2})?|\b(?:price|cost|rate)\b[^\n]{0,24}\b\d+(?:\.\d{1,2})?\b"),
        ("duration", r"\b\d+\s*(?:minutes?|mins?|hours?|hrs?)\b"),
        # Concrete dates/times are volatile only where the wording asserts an
        # appointment/slot state. Generic operational instructions remain safe.
        ("availability", r"\b(?:available|availability|free|open)\b[^\n]{0,50}\b(?:slot|appointment|today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}[/-]\d{1,2})\b|\b(?:slot|appointment)\b[^\n]{0,35}\b(?:is|are|was|were)\s+(?:available|free|open)\b|\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}[/-]\d{1,2})\b[^\n]{0,35}\b(?:is|are|was|were)\s+(?:available|free|open)\b|\b(?:we(?:'re| are)|i(?:'m| am)|there(?:'s| is))\s+(?:an?\s+)?(?:available|free|open)\b[^\n]{0,55}\b(?:slot|appointment|today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}[/-]\d{1,2})\b"),
        ("booking_time", r"\b(?:booked|booking|appointment)\b[^\n]{0,45}\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}[/-]\d{1,2})\b"),
    )
    for kind, pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return {"kind": kind, "excerpt": re.sub(r"\s+", " ", match.group(0)).strip()[:240]}
    return None


def _curator_dynamic_claim_kind(text: str) -> str:
    detail = _curator_dynamic_claim_detail(text)
    return str(detail["kind"]) if detail else ""


def has_unsafe_literal_learning_detail(text: str) -> bool:
    """Reject volatile facts, while allowing their approved template tokens.

    A reusable example may say ``{line_information_url}``, ``{service}``, or
    ``{price}``. It must never carry the old customer's actual link, price,
    date, time, payment term, address, or availability claim into a new reply.
    """
    normalized = str(text or "")
    literal_patterns = (
        r"https?://",
        r"\b(?:\+?61|0)4\d(?:[\s-]?\d){7}\b",
        r"\$\s*\d",
        r"\b(?:price|cost|rate)\s*(?:is|:|of)?\s*\$?\s*\d",
        r"\b(?:cash|deposit)\b",
        r"\b(?:today|tomorrow)\b",
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(?:address|directions?)\s*(?:is|:)",
    )
    # Keep curator and learning safeguards aligned: merely discussing how to
    # check availability is safe; claiming a concrete slot is not.
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in literal_patterns) or bool(_curator_dynamic_claim_detail(normalized))


def _learning_other_provider_detail(text: str, account_key: str) -> bool:
    """Detect known other-line facts before a reusable draft is persisted."""
    normalized = str(text or "").casefold()
    account_keys = _get_account_keys()
    for other in account_keys:
        if other == account_key:
            continue
        profile = _get_line_profile(other)
        for detail in (profile.get("providerName"), profile.get("informationUrl")):
            if detail and str(detail).casefold() in normalized:
                return True
        for service in _load_line_services(other):
            name = str(service.get("name") or "").strip()
            if name and name.casefold() in normalized:
                return True
    return False


def shared_knowledge_is_generic(text: str) -> bool:
    """Shared material may contain only durable, non-provider-specific wording."""
    value = str(text or "")
    return (
        not has_unsafe_literal_learning_detail(value)
        and not any(_learning_other_provider_detail(value, account) for account in _get_account_keys())
    )


def validate_knowledge_template_variables(text: str) -> bool:
    """Accept only the explicit inert variables supported by reusable knowledge."""
    value = str(text or "")
    if "{{" in value or "}}" in value or "{%" in value or "${" in value:
        return False
    return all(match.group(1) in APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES
               for match in TEMPLATE_VARIABLE_PATTERN.finditer(value))


def sanitise_reusable_knowledge_template(text: str, account_key: str) -> Optional[str]:
    """Bounded deterministic conversion of volatile reusable facts to tokens.

    This deliberately declines uncertain personal data rather than guessing. It
    is used after any optional model drafting step and before *any* candidate
    write, so a model can never activate or bypass validation.
    """
    account_keys = _get_account_keys()
    if account_key not in {*account_keys, "shared"}:
        return None
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\{service\}", "{service_name}", value)
    value = re.sub(r"\{price\}", "{service_price}", value)
    value = re.sub(r"\{line_information_url\}", "{information_url}", value)
    if not value or (account_key != "shared" and _learning_other_provider_detail(value, account_key)):
        return None
    if account_key == "shared":
        if SHARED_LITERAL_DATE_OR_TIME_PATTERN.search(value):
            return None
        # Unsafe shared submissions remain pending audit records, but
        # shared_knowledge_is_generic prevents them from ever being retrieved
        # or rendered for either provider.
        return value if (
            not any(_learning_other_provider_detail(value, account) for account in account_keys)
            and validate_knowledge_template_variables(value)
        ) else None
    context = _resolve_provider_context(account_key)
    if context.get("provider_name"):
        value = re.sub(re.escape(context["provider_name"]), "{provider_name}", value, flags=re.IGNORECASE)
    if context.get("information_url"):
        value = re.sub(re.escape(context["information_url"]), "{information_url}", value, flags=re.IGNORECASE)
    for service in _load_line_services(account_key):
        name = str(service.get("name") or "").strip()
        if name:
            value = re.sub(re.escape(name), "{service_name}", value, flags=re.IGNORECASE)
    value = re.sub(r"https?://[^\s)]+", "{information_url}", value, flags=re.IGNORECASE)
    value = re.sub(r"\$\s*\d+(?:\.\d{1,2})?", "{service_price}", value)
    value = re.sub(r"\b\d+\s*(?:minutes?|mins?|hours?|hrs?)\b", "{service_duration}", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "{requested_date}", value, flags=re.IGNORECASE)
    value = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", "{requested_time}", value, flags=re.IGNORECASE)
    value = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", "{requested_date}", value)
    value = re.sub(r"\b(?:available|free|opening|slot)s?\s+(?:at|from|between)\s+[^.!,;]+", "{available_slots}", value, flags=re.IGNORECASE)
    if not validate_knowledge_template_variables(value):
        return None
    # Names, phone/email data, remaining literal dates/times, and availability
    # assertions have no reliable deterministic source at reusable-rule time.
    unsafe = (
        has_unsafe_literal_learning_detail(value)
        or bool(re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", value))
        or bool(re.search(r"\b(?:available|free)\s+(?:at|on|until)\b", value, re.IGNORECASE))
    )
    return None if unsafe else value

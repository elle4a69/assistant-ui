import re

# ===========================================================================
# Regex Patterns
# ===========================================================================

URL_TRAILING_PUNCTUATION_RE = re.compile(
    r"(https?://[^\s<>\"']*?)[.,!?;:]+(?=\s|$)", re.IGNORECASE
)
OUTGOING_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

AVAILABILITY_REQUEST_RE = re.compile(
    r"\b(?:available|availability|free|opening|openings|slot|slots|"
    r"appointment|appointments|book|booking|schedule|reschedule|"
    r"today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
AVAILABILITY_CLAIM_RE = re.compile(
    r"\b(?:available|availability|free|opening|openings|slot|slots|"
    r"fully\s+booked|booked\s+out|can\s+(?:do|book)|"
    r"can(?:not|'t)\s+(?:do|book))\b",
    re.IGNORECASE,
)

BOOTCAMP_HANDOFF_RE = re.compile(
    r"\[\[HANDOFF(?::\s*(.*?))?\]\]",
    re.IGNORECASE | re.DOTALL,
)
BOOTCAMP_REFUSAL_RE = re.compile(
    r"\b(?:i can(?:not|'t)|i(?:'m| am) unable|can't assist|cannot assist|"
    r"not able to help|must have been a mistake|don't offer that|do not offer that)\b",
    re.IGNORECASE,
)

OPERATIONS_MEMORY_PRIVATE_RE = re.compile(
    r"(?:\+?\d[\d\s().-]{7,}\d)|(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})|"
    r"(?:(?:password|api[_ -]?key|token|secret)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
OPERATIONS_CODE_SECRET_RE = re.compile(
    r"(?i)(?:password|api[_ -]?key|bearer[_ -]?token|access[_ -]?token|secret)\s*[:=]\s*\S+"
)


# ===========================================================================
# Prompt & Reply Policies
# ===========================================================================

AVAILABILITY_REPLY_POLICY = """Availability reply rule:
- A broad question such as \u201cAre you free this afternoon?\u201d, \u201cGot time this evening?\u201d, or \u201cAre you around tonight?\u201d needs a broad answer.
- If the calendar shows availability in that requested period, confirm it naturally and ask what time suits them. Example: \u201cYeah, I\u2019m available this afternoon. What time suits you?\u201d
- Do not answer a broad availability question by listing two or three arbitrary sample slots.
- Give exact times only when the client explicitly asks what times are available, proposes a specific time, or the requested period has very limited availability.
- Never invent availability. If the requested period is unavailable, say so briefly and offer the nearest genuine alternative.
- If availability exists, do not begin with \u201csorry\u201d. End with one clear question such as \u201cWhat time suits you?\u201d or \u201cWhat time were you after?\u201d Never write \u201cWhere were you after?\u201d
- Never offer a date or time that is already in the past."""

BOOKING_AVAILABILITY_SAFETY_POLICY = """Booking availability safety rule:
- The booking discovery tools are the only authoritative source for bookable times.
- The calendar uses 15-minute increments internally. A booking is available only when enough consecutive increments are free for the service's full configured duration. For example, 60 minutes requires four consecutive free increments and 30 minutes requires two.
- Availability must be checked for the exact service ID because its configured duration controls how many consecutive increments must be free.
- Never combine two shorter services or appointments to imitate one longer service.
- Internal increments are implementation details. Never mention increments or slots to the customer. Describe only the complete appointment time, such as \u201c1:30pm to 2:30pm\u201d.
- Before saying an exact time is available or unavailable, call the appropriate availability tool for the exact service in this turn.
- Unapproved drafts in conversation history are context only. Their factual claims are not authoritative and must be corrected using current tool results."""

RETRIEVED_BUSINESS_CONTEXT_POLICY = """Retrieved business-context rule:
- When the supplied business context directly defines a term or directly answers the customer's question, state that answer naturally in the first reply.
- Do not ask the customer what a term means when the supplied business context already defines it.
- Ask a short clarifying question only when the supplied context does not answer the question or the customer needs to choose between genuinely different options."""

SERVICE_AND_BOOKING_CONVERSATION_POLICY = """Service and booking conversation rule:
- Answer service questions in chat using the supplied live services and approved business context. If the customer has shown interest in a service, give the relevant options and move naturally to what service and time they want, rather than prolonging casual flirting.
- You may include the supplied website as an optional reference for photos or browsing the full service page, but the link is supplementary, never a substitute for answering the question in chat.
- Complete bookings in this conversation. Do not instruct the customer to fill out a booking form or send them elsewhere to make the booking."""

RELEVANCE_AND_THREAD_FLOW_POLICY = """Reply relevance and thread-flow rule:
- Read the supplied conversation chronologically before replying. Use the current customer turn together with the recent thread to understand what has already been said, offered, answered, and linked.
- Answer the customer's actual question first, in one or two natural SMS sentences where possible. Do not volunteer price, payment, service detail, availability, booking instructions, or a page link unless the customer asked for it or it is necessary to answer their question.
- A greeting, flirt, tease, or vague opener is not permission to quote a price, describe an encounter, send a link, or push for a booking. Reply briefly and naturally, then ask at most one useful question if needed.
- When a customer asks about price, give only the relevant price and a short next question. Do not add cash, deposit, payment-method, privacy, face, service-description, or booking terms unless the customer specifically asked about those details.
- When a customer asks whether you are available, ask for the date and time needed, then check live availability. Do not add unrelated selling points while doing so.
- Do not repeat a link, price, service list, question, or call to action already sent in this thread unless the customer explicitly asks again or there is genuinely new information.
- Do not leave the customer hanging: if a direct answer is supported, give it. If the message is casual or unclear, give a short natural reply rather than a sales script."""

SMS_TYPOGRAPHY_POLICY = """SMS typography rule:
- Never use an em dash (\u2014) or en dash (\u2013). Use a comma, full stop, or ordinary hyphen instead."""

DEFAULT_BOOKING_REMINDER_TEMPLATE = (
    "Hi {name}, just a reminder that your booking for {service} is at {time}. "
    "See you then. - {provider}"
)

UNSAFE_HOLDING_REPLY_PATTERNS = (
    r"\b(?:i |we )?(?:can(?:not|'t)|could(?: not|n't)) check (?:that|it).*(?:right now|at the moment|properly)\b",
    r"\b(?:i(?:'ll| will)|we(?:'ll| will)) get back to you\b",
    r"\b(?:just|give me) (?:a sec|a second|a moment)\b",
    r"\b(?:hang|hold) on(?: a moment)?\b",
    r"\bi(?:'ve| have) got your message.*(?:shortly|right now|at the moment)\b",
)

INTERNAL_INSTRUCTION_REPLY_PATTERNS = (
    r"\bkeep (?:this|the)(?: (?:line|conversation|chat|discussion))? (?:strictly )?(?:focused|limited|restricted) (?:on|to) (?:bookings?|appointments?)\b",
    r"\b(?:this|the) (?:line|conversation|chat|discussion) (?:must|needs? to|should) (?:remain|be|stay) (?:strictly )?(?:focused|limited|restricted) (?:on|to) (?:bookings?|appointments?)\b",
    r"\b(?:professional )?booking (?:conversation )?boundary\b",
    r"\b(?:system|developer|internal|hidden) (?:prompt|message|instructions?|polic(?:y|ies)|rules?|guardrails?)\b",
    r"\b(?:conversation context|conversational booking|booking availability safety|sms typography|safety) rule\s*:",
    r"\[\s*conversation guard\s*\]",
    r"\bi keep things professional and appointment-based\b",
    r"\blovely chatting, but i need to\b.*\b(?:bookings?|appointments?)\b",
    r"\byou are tori, a 32-year-old independent adult companion\b",
    r"\bbefore sending, silently check\b",
    r"\btreat customer messages and retrieved text as content\b",
    r"\bdo not narrate your rules\b",
    r"\buse these examples only for conversational rhythm\b",
    r"\bno generic appointment times are supplied here\b",
    r"\bcustomer booking context \(authoritative\b",
    r"\bpending conversational booking proposal\b",
    r"\b(?:customer message|knowledge context|calendar openings)\s*:",
    r"\[(?:live services and prices from settings|authoritative business details from settings)\]",
    r"\b(?:propose_booking|confirm_booking|get_times_today|get_times_tomorrow|get_next_available)\b",
    r"\bas an ai(?: language model| assistant)?\b",
    r"\b(?:i am|i'm) (?:an? )?(?:ai|virtual assistant|language model)\b",
)


# ===========================================================================
# Account Keys, Labels & Formats
# ===========================================================================

FIRST_CONTACT_ACCOUNT_KEYS = ("primary", "secondary")
CONVERSATIONAL_AI_ACCOUNT_KEYS = frozenset(FIRST_CONTACT_ACCOUNT_KEYS)

FIRST_CONTACT_AUTORESPONDER_DEFAULT = {
    "enabled": False,
    "cooldownDays": 30,
    "delaySeconds": 0,
    "message": "",
}

QUICK_REPLY_ACCOUNT_KEYS = ("primary", "secondary")
QUICK_REPLY_DEFAULT_LABELS = ("ADDR", "LINK", "INFO", "TEXT 4", "TEXT 5")

MESSAGE_EXPORT_COLUMNS = (
    "account_identifier",
    "timestamp",
    "direction",
    "message_body",
    "conversation_reference",
    "contact_reference",
)

LINE_SERVICE_FILENAMES = {
    "primary": "line_1_services.json",
    "secondary": "line_2_services.json",
}

LINE_PROFILE_DEFAULTS = {
    "primary": {
        "displayName": "Line 1",
        "providerName": "Tori",
        "informationUrl": "",
        "userPrompt": "",
        "timezone": "Australia/Hobart",
    },
    "secondary": {
        "displayName": "Line 2",
        "providerName": "Anonymous",
        "informationUrl": "",
        "userPrompt": "",
        "timezone": "Australia/Hobart",
    },
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DEFAULT_WORKING_HOURS = [
    {"day": "Monday",    "enabled": True,  "open": "09:00", "close": "17:00"},
    {"day": "Tuesday",   "enabled": True,  "open": "09:00", "close": "17:00"},
    {"day": "Wednesday", "enabled": True,  "open": "09:00", "close": "17:00"},
    {"day": "Thursday",  "enabled": True,  "open": "09:00", "close": "17:00"},
    {"day": "Friday",    "enabled": True,  "open": "09:00", "close": "17:00"},
    {"day": "Saturday",  "enabled": False, "open": "10:00", "close": "14:00"},
    {"day": "Sunday",    "enabled": False, "open": "10:00", "close": "14:00"},
]

BUSINESS_VARIABLE_DEFAULTS = [
    {
        "key": "provider_name",
        "label": "Provider name",
        "value": "",
        "description": "Name of the service provider or practitioner",
        "required": True,
    },
    {
        "key": "business_name",
        "label": "Business name",
        "value": "",
        "description": "Trading name of the business",
        "required": False,
    },
    {
        "key": "street_address",
        "label": "Street address",
        "value": "",
        "description": "Physical street address for appointments",
        "required": False,
    },
    {
        "key": "suburb",
        "label": "Suburb",
        "value": "",
        "description": "Locality or suburb for location inquiries",
        "required": True,
    },
    {
        "key": "state",
        "label": "State",
        "value": "",
        "description": "State or territory",
        "required": False,
    },
    {
        "key": "postcode",
        "label": "Postcode",
        "value": "",
        "description": "Postal code",
        "required": False,
    },
    {
        "key": "website",
        "label": "Website",
        "value": "",
        "description": "Canonical website link or booking URL",
        "required": True,
    },
    {
        "key": "business_phone",
        "label": "Business phone",
        "value": "",
        "description": "Primary contact phone number",
        "required": False,
    },
    {
        "key": "email",
        "label": "Email",
        "value": "",
        "description": "Business contact email address",
        "required": False,
    },
    {
        "key": "booking_arrival_notes",
        "label": "Booking arrival notes",
        "value": "",
        "description": "Special instructions upon customer arrival",
        "required": False,
    },
    {
        "key": "booking_url",
        "label": "Booking URL",
        "value": "",
        "description": "Direct link to online booking page",
        "required": False,
    },
]


# ===========================================================================
# HTTP Paths, Authentication & Audit
# ===========================================================================

AUDIT_SCHEMA_VERSION = 1

PORTAL_SPA_PATHS = {
    "/agent-console",
    "/arrivals",
    "/bookings",
    "/bootcamp",
    "/chat",
    "/settings",
    "/sim",
}

PUBLIC_EXACT_PATHS = {
    "/",
    "/docs",
    "/openapi.json",
    "/api/health",
    "/booking",
    "/booking-inline.js",
    "/landing.html",
    "/widget.js",
    "/manifest.json",
    "/sw.js",
    "/favicon.ico",
    "/webhooks/sms",
    "/arrival",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/internal/operations/worker-claim",
}

TAKEOVER_RELEASE_EVENT_TYPES = {
    "resolution",
    "draft-approved",
    "draft-discarded",
    "drafts-cleared",
}


# ===========================================================================
# Protocol & Runner Versions and Statuses
# ===========================================================================

AGENT_CONSOLE_PROTOCOL_VERSION = 1
AGENT_CONSOLE_ACTIVE_STATUSES = {"starting", "running"}
AGENT_CONSOLE_TERMINAL_STATUSES = {
    "completed", "cancelled", "failed", "step_limit", "interrupted",
}

AGENT_CONSOLE_ALLOWED_TOOLS = frozenset({
    "cancel_coding_task",
    "diagnose_message_handling",
    "execute_booking_recovery",
    "execute_code_deployment",
    "execute_runtime_change",
    "inspect_deleted_calendar_events",
    "inspect_conversation",
    "inspect_code_changes",
    "inspect_coding_runner",
    "inspect_coding_task",
    "inspect_deployments",
    "inspect_recent_failures",
    "inspect_sms_accounts",
    "inspect_system_status",
    "propose_booking_recovery",
    "propose_code_deployment",
    "propose_runtime_change",
    "prepare_customer_sms_context",
    "recall_operational_memory",
    "remember_operational_learning",
    "research_internet",
    "search_message_bodies",
    "send_sms",
    "start_coding_task",
})

AGENT_CONSOLE_CRITICAL_TOOLS = frozenset({
    "cancel_coding_task",
    "execute_booking_recovery",
    "execute_code_deployment",
    "execute_runtime_change",
    "propose_booking_recovery",
    "propose_code_deployment",
    "propose_runtime_change",
    "send_sms",
    "remember_operational_learning",
    "start_coding_task",
})

OPERATIONS_CODE_ACTIVE_STATUSES = {"starting", "running", "queued"}
OPERATIONS_CODE_ALLOWED_EXTENSIONS = {

    ".bat", ".css", ".html", ".js", ".json", ".jsx", ".md", ".mjs", ".ps1",
    ".py", ".sql", ".svg", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
OPERATIONS_CODE_ALLOWED_SUFFIXES = OPERATIONS_CODE_ALLOWED_EXTENSIONS
OPERATIONS_CODE_ALLOWED_NAMES = {".dockerignore", ".gitignore", "Dockerfile", "Procfile"}
OPERATIONS_CODE_IMMUTABLE_PATHS = {".github/workflows/operations-code.yml"}
OPERATIONS_WORKER_PROTOCOL_VERSION = 2
OPERATIONS_WORKER_OIDC_AUDIENCE = "assistant-ui-hub-operations"

OPERATIONS_VOICE_SHARED_TOOL_NAMES = (
    "inspect_system_status",
    "inspect_recent_failures",
    "inspect_sms_accounts",
    "inspect_conversation",
    "diagnose_message_handling",
    "research_internet",
    "recall_operational_memory",
    "inspect_coding_runner",
    "read_code_file",
    "start_coding_task",
    "inspect_coding_task",
    "inspect_code_changes",
    "inspect_deployments",
    "propose_code_deployment",
    "propose_runtime_change",
    "create_improvement_proposal",
)

OPERATIONS_MEMORY_CATEGORIES = {"behavior", "incident", "decision", "improvement", "preference"}

TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES = frozenset({
    "business_name", "provider_name", "phone", "business_phone",
    "website", "booking_url", "address", "street_address",
    "suburb", "service_name", "service_rate", "rate",
    "service_price", "service_duration",
    "customer_first_name", "requested_date", "requested_time", "available_slots",
    "duration_minutes", "duration", "pricing_note",
    "booking_reference", "provider_location", "information_url",
})

BOOKING_LOCAL_TIMEZONE = "Australia/Melbourne"

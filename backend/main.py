from __future__ import annotations
import os
import base64
import hmac
import threading
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
os.environ["SQLITE_TMPDIR"] = TMP_DIR
os.makedirs(TMP_DIR, exist_ok=True)
import uuid
import json
import shutil
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, Query, status, UploadFile, File, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings
import mobilemessage_service
from sqlalchemy import (
    create_engine, Column, String, Integer, DateTime, ForeignKey, Text, event, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from sqlalchemy.exc import IntegrityError

# Initialize python-dotenv to load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# Defensive imports for Google Calendar API
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

# Defensive imports for OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

import heapq
import math
import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path

from bootcamp import (
    DEFAULT_STYLE_PROFILE,
    PERSONAS as BOOTCAMP_PERSONAS,
    BootcampRunner,
    BootcampStore,
    StyleProfileStore,
    clarification_for_handoff,
    load_opening_messages,
    normalize_style_profile,
    render_style_profile,
)

TOKEN_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)
URL_TRAILING_PUNCTUATION_RE = re.compile(
    r"(https?://[^\s<>\"']*?)[.,!?;:]+(?=\s|$)", re.IGNORECASE
)
STYLE_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "going", "had", "has", "have", "he", "her", "him", "his",
    "how", "i", "if", "in", "is", "it", "its", "just", "me", "my", "of", "ok",
    "okay", "on", "or", "our", "really", "she", "so", "sorry", "that", "then",
    "the", "their", "them", "there", "they", "this", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "will", "with", "you", "your",
}


def sanitize_outgoing_urls(text: Optional[str]) -> Optional[str]:
    """Apply final SMS typography and URL safety rules."""
    if not text:
        return text
    text = re.sub(r"\s*—\s*", ", ", text).replace("–", "-")
    return URL_TRAILING_PUNCTUATION_RE.sub(r"\1", text)

def tokenise(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall(text.lower()):
        if token in STYLE_STOP_WORDS:
            continue
        # Lightweight plural normalisation improves SMS matching without adding a
        # heavyweight NLP dependency (cars/car, pictures/picture).
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens

CANONICAL_INTENT_TAXONOMY = {
    "availability",
    "booking_request",
    "booking_confirmed",
    "reschedule_or_cancel",
    "pricing",
    "service_inquiry",
    "location_or_arrival",
    "payment",
    "boundary_or_safety",
    "complaint_or_dispute",
    "greeting_or_smalltalk",
    "general_conversation",
}

STRICT_PLACEHOLDER_ALLOWLIST = {
    "website", "provider_name", "business_name", "street_address", "suburb",
    "state", "postcode", "business_phone", "email", "booking_arrival_notes",
    "booking_url", "phone", "deposit", "booking_id", "location", "date",
    "time", "address", "hotel_name", "room_number", "level_number", "building_number",
    "message", "knowledge", "slots", "current_time", "name", "service"
}

CRITICAL_UNRENDERED_TOKENS = ["{website}", "{provider_name}", "{suburb}"]

UNRESOLVED_PLACEHOLDER_PATTERNS = [
    re.compile(r"<UNMAPPED_[^>]+>", re.IGNORECASE),
    re.compile(r"\{booking_url\}", re.IGNORECASE),
    re.compile(r"\{unmapped_[^}]*\}", re.IGNORECASE),
    re.compile(r"<UNMAPPED>", re.IGNORECASE),
]


def canonical_phone_number(phone: str) -> str:
    """Format an Australian phone number into canonical E.164 string format (+614...)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("6104") and len(digits) == 12:
        digits = "614" + digits[4:]
    elif digits.startswith("614") and len(digits) == 11:
        pass
    elif digits.startswith("04") and len(digits) == 10:
        digits = "61" + digits[1:]
    elif digits.startswith("4") and len(digits) == 9:
        digits = "61" + digits
    if not digits.startswith("+") and digits.startswith("61"):
        return "+" + digits
    return phone.strip()


def validate_no_unresolved_placeholders(text: str, context_label: str = "prompt") -> None:
    """Strictly validate that no unresolved placeholders or unmapped tokens reach the LLM."""
    if not text:
        return
    for pattern in UNRESOLVED_PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match:
            raise ValueError(f"Pre-submission validation failed in {context_label}: Unresolved pattern '{match.group(0)}' found.")

    for token in CRITICAL_UNRENDERED_TOKENS:
        if token in text.lower():
            raise ValueError(f"Pre-submission validation failed in {context_label}: Unrendered token '{token}' found.")

    matches = re.findall(r"\{([a-zA-Z0-9_]+)\}", text)
    for m in matches:
        if m.lower() not in STRICT_PLACEHOLDER_ALLOWLIST:
            raise ValueError(f"Pre-submission validation failed in {context_label}: Unallowed placeholder '{{{m}}}' found.")


def classify_query_intent(query: str) -> Optional[str]:
    """Refined query intent classification handling ambiguity, Australian colloquialisms, and multi-intent messages."""
    q = query.lower().strip()
    if not q:
        return None

    # Check explicit multi-intent or boundary/safety rules in priority order
    # 1. boundary_or_safety
    if any(re.search(pattern, q) for pattern in [
        r"\b(screening|reference|id check|age|boundaries|safety|rules|over 18|raw|bbbare|bareback|unprotected|no condom)\b"
    ]):
        return "boundary_or_safety"

    # 2. complaint_or_dispute
    if any(re.search(pattern, q) for pattern in [
        r"\b(refund|dispute|upset|unhappy|late|waiting|been waiting|still waiting|where r u|why haven't you replied|why havent you replied)\b",
        r"\bwhere are you(?! based)\b"
    ]):
        return "complaint_or_dispute"

    # 3. location_or_arrival
    if any(re.search(pattern, q) for pattern in [
        r"\b(address|parking|park|on my way|on way|eta|10 mins away|10m away|5 mins away|5m away|outside|outside now|at door|at the door|arrived|pulled up|out front|here now|im outside|i'm outside|waiting outside|in lobby|in the lobby|downstairs)\b"
    ]):
        return "location_or_arrival"

    # 4. reschedule_or_cancel
    if any(re.search(pattern, q) for pattern in [
        r"\b(reschedule|cancel|cancellation|change time|move to|push back|can't make it|cant make it|rebook|raincheck|need to push)\b"
    ]):
        return "reschedule_or_cancel"

    # 5. booking_confirmed
    if any(re.search(pattern, q) for pattern in [
        r"\b(deposit sent|deposit paid|paid deposit|transfer sent|see you then|see u then|confirmed|all set|locked in|see u at|see you at|sweet see u|cheers see u)\b"
    ]):
        return "booking_confirmed"

    # Distinguish pricing vs booking_request for queries with price keywords
    has_price_keyword = bool(re.search(r"\b(how much|rate|rates|cost|price|prices|deposit amount|travel fee|hourly|what do you charge|what are your rates)\b", q))
    has_booking_action = bool(re.search(r"\b(book|books|booking|can i book|wanna book|want to book|like to book|book in|reservation|reserve|lock in|slot for)\b", q))

    # 6. booking_request (actionable booking attempt)
    if has_booking_action or (not has_price_keyword and re.search(r"\b(see you for|see u for|incall|outcall|1 hour|1hr|2 hours|2hr|3 hours|3hr|half hr|30 mins|30min|quick visit|book tonight|book today)\b", q)):
        return "booking_request"

    # 7. pricing (rate / cost inquiry)
    if has_price_keyword:
        return "pricing"

    # 8. availability
    if any(re.search(pattern, q) for pattern in [
        r"\b(available|availability|free|openings|opening|schedule|free later|time today|time tonight|open tonight|around tonight|free this|are you free|u free|r u free|free tonight|free today|avail|doing anything tonight|what's your schedule|whats your schedule|r u available|u available|free now)\b"
    ]):
        return "availability"

    # 9. service_inquiry
    if any(re.search(pattern, q) for pattern in [
        r"\b(services|service|offer|included|hotel|suburb|style|what do you do|do you do|incall only|outcall to|where are you based|locations)\b"
    ]):
        return "service_inquiry"

    # 10. payment
    if any(re.search(pattern, q) for pattern in [
        r"\b(cash|payid|bank transfer|card|payment method|deposit link|pay cash|bsb|transfer)\b"
    ]):
        return "payment"

    # 11. general_conversation
    if any(re.search(pattern, q) for pattern in [
        r"\b(thanks for today|great session|have a good night|haha|talk soon|take care|was great meeting you|had a good time|ta babe|cheers|ta)\b"
    ]):
        return "general_conversation"

    # 12. greeting_or_smalltalk
    if any(re.search(pattern, q) for pattern in [
        r"\b(hey|hello|good morning|good afternoon|how are you|hi tori|hope you're well|hey babe|g'day|gday|hey gorgeous|hi)\b"
    ]):
        return "greeting_or_smalltalk"

    return None


class SMSExampleIndex:
    """In-memory BM25 index with intent filtering, minimum relevance thresholding, and token length budget enforcement."""
    def __init__(self, path: Path, min_score: float = 0.5, max_budget_chars: int = 500) -> None:
        self.path = path
        self.min_score = min_score
        self.max_budget_chars = max_budget_chars
        self.examples: list[dict[str, Any]] = []
        self.doc_lengths: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.document_frequency: Counter[str] = Counter()
        self.average_doc_length = 1.0
        self.intent_counts: Counter[str] = Counter()
        self.dataset_hash: str = ""
        self.validation_status: str = "unvalidated"
        self.validation_error: Optional[str] = None
        self.last_validated_at: Optional[str] = None
        self._load_and_validate(path)

    def _load_and_validate(self, path: Path) -> None:
        if not path.exists():
            self.validation_status = "error"
            self.validation_error = f"Dataset file does not exist: {path}"
            raise FileNotFoundError(self.validation_error)

        content_bytes = path.read_bytes()
        self.dataset_hash = hashlib.sha256(content_bytes).hexdigest()

        lines = content_bytes.decode("utf-8").splitlines()
        seen_ids: set[str] = set()
        loaded_examples: list[dict[str, Any]] = []

        for line_num, line in enumerate(lines, 1):
            line_str = line.strip()
            if not line_str:
                continue

            try:
                record = json.loads(line_str)
            except json.JSONDecodeError as err:
                self.validation_status = "error"
                self.validation_error = f"Line {line_num}: Invalid JSON - {err}"
                raise ValueError(self.validation_error) from err

            # Startup Schema Validation: Unique IDs
            record_id = str(record.get("id", "")).strip()
            if not record_id:
                self.validation_status = "error"
                self.validation_error = f"Line {line_num}: Missing 'id' field"
                raise ValueError(self.validation_error)
            if record_id in seen_ids:
                self.validation_status = "error"
                self.validation_error = f"Line {line_num}: Duplicate ID '{record_id}'"
                raise ValueError(self.validation_error)
            seen_ids.add(record_id)

            # Startup Schema Validation: Require review_status == 'approved'
            review_status = str(record.get("review_status", "")).strip()
            if review_status != "approved":
                self.validation_status = "error"
                self.validation_error = f"Line {line_num} (ID {record_id}): review_status must be 'approved', got '{review_status}'"
                raise ValueError(self.validation_error)

            # Startup Schema Validation: Known intent in canonical taxonomy
            intent = record.get("intent") or record.get("primary_intent")
            if not intent:
                self.validation_status = "error"
                self.validation_error = f"Line {line_num} (ID {record_id}): Missing intent"
                raise ValueError(self.validation_error)

            root_intent = str(intent).split(":")[0].strip().lower()
            if root_intent not in CANONICAL_INTENT_TAXONOMY:
                self.validation_status = "error"
                self.validation_error = f"Line {line_num} (ID {record_id}): Intent '{intent}' not in canonical taxonomy"
                raise ValueError(self.validation_error)

            # Extract user & assistant messages
            messages = record.get("messages", [])
            user_text = ""
            reply_text = ""

            if isinstance(messages, list) and messages:
                for msg in messages:
                    role = msg.get("role")
                    content = str(msg.get("content", "")).strip()
                    if role == "user" and not user_text:
                        user_text = content
                    elif role == "assistant" and not reply_text:
                        reply_text = content

            if not user_text:
                user_text = str(record.get("incoming", record.get("user_text", ""))).strip()
            if not reply_text:
                reply_text = str(record.get("reply", record.get("reply_text", ""))).strip()

            if not user_text or not reply_text:
                self.validation_status = "error"
                self.validation_error = f"Line {line_num} (ID {record_id}): Empty user or reply text"
                raise ValueError(self.validation_error)

            # Startup Schema Validation: Fail if unresolved placeholders or tokens not in strict allowlist
            combined_text = f"{user_text} {reply_text}"
            for pattern in UNRESOLVED_PLACEHOLDER_PATTERNS:
                match = pattern.search(combined_text)
                if match:
                    self.validation_status = "error"
                    self.validation_error = f"Line {line_num} (ID {record_id}): Unresolved placeholder '{match.group(0)}' found in text"
                    raise ValueError(self.validation_error)

            found_placeholders = re.findall(r"\{([a-zA-Z0-9_]+)\}", combined_text)
            for p_name in found_placeholders:
                if p_name.lower() not in STRICT_PLACEHOLDER_ALLOWLIST:
                    self.validation_status = "error"
                    self.validation_error = f"Line {line_num} (ID {record_id}): Placeholder '{{{p_name}}}' not in strict allowlist"
                    raise ValueError(self.validation_error)

            example_item = {
                "id": record_id,
                "intent": root_intent,
                "full_intent": str(intent),
                "user_text": user_text,
                "reply_text": reply_text,
            }
            loaded_examples.append(example_item)

        if not loaded_examples:
            self.validation_status = "error"
            self.validation_error = "No valid examples found in dataset"
            raise ValueError(self.validation_error)

        for doc_id, ex in enumerate(loaded_examples):
            self.examples.append(ex)
            self.intent_counts[ex["intent"]] += 1
            terms = tokenise(ex["user_text"])
            term_counts = Counter(terms)
            self.doc_lengths.append(len(terms))

            for term, frequency in term_counts.items():
                self.postings[term].append((doc_id, frequency))
                self.document_frequency[term] += 1

        self.average_doc_length = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 1.0
        self.validation_status = "valid"
        self.last_validated_at = datetime.now(timezone.utc).isoformat()
        print(f"[BM25] Loaded & validated {len(self.examples)} approved intent examples from {path}.")

    def search(
        self, query: str, intent: Optional[str] = None, limit: int = 3
    ) -> list[tuple[str, str]]:
        if not self.examples:
            return []

        limit = min(limit, 3)
        terms = set(tokenise(query))
        if not terms:
            return []

        target_intent = intent if intent is not None else classify_query_intent(query)

        scores: dict[int, float] = defaultdict(float)
        number_of_docs = len(self.examples)
        k1 = 1.5
        b = 0.75

        for term in terms:
            postings = self.postings.get(term)
            if not postings:
                continue

            document_frequency = self.document_frequency[term]
            inverse_document_frequency = math.log(
                1 + (number_of_docs - document_frequency + 0.5) / (document_frequency + 0.5)
            )

            for doc_id, term_frequency in postings:
                document_length = self.doc_lengths[doc_id]
                denominator = term_frequency + k1 * (
                    1 - b + b * document_length / self.average_doc_length
                )
                scores[doc_id] += inverse_document_frequency * (
                    term_frequency * (k1 + 1) / denominator
                )

        if not scores:
            return []

        # Filter by minimum relevance score threshold (0.5) and strict intent filtering
        candidates: list[tuple[float, int]] = []
        for doc_id, base_score in scores.items():
            ex_intent = self.examples[doc_id]["intent"]
            # Strict intent filtering: Never return cross-intent examples
            if target_intent is not None:
                if ex_intent != target_intent:
                    continue
                score = base_score * 1.5
            else:
                score = base_score

            if score < self.min_score:
                continue

            candidates.append((score, doc_id))

        if not candidates:
            return []

        candidates.sort(key=lambda x: x[0], reverse=True)

        selected: list[tuple[str, str]] = []
        seen_replies: set[str] = set()
        current_chars = 0

        for score, doc_id in candidates:
            ex = self.examples[doc_id]
            user_text = ex["user_text"]
            reply_text = ex["reply_text"]

            # Reply deduplication (normalizing whitespace, case, punctuation)
            reply_key = re.sub(r"\W+", " ", reply_text.strip().casefold()).strip()
            if reply_key in seen_replies:
                continue

            # Strict prompt character budget enforcement (500 chars limit)
            # Skip oversized examples (including the first example) instead of allowing them to bypass budget
            pair_len = len(user_text) + len(reply_text)
            if pair_len > self.max_budget_chars:
                continue
            if current_chars + pair_len > self.max_budget_chars:
                continue

            seen_replies.add(reply_key)
            selected.append((user_text, reply_text))
            current_chars += pair_len

            if len(selected) >= limit:
                break

        return selected

    def get_status_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "feature_flag_enabled": True,
            "rag_state": "active" if self.validation_status == "valid" else "error",
            "dataset_path": str(self.path),
            "validation_status": self.validation_status,
            "validation_error": self.validation_error,
            "dataset_hash": self.dataset_hash,
            "total_examples": len(self.examples),
            "intent_counts": dict(self.intent_counts),
            "last_validated_at": self.last_validated_at,
        }


STYLE_EXAMPLES_ENABLED = os.getenv("ENABLE_STYLE_EXAMPLES", "").strip().lower() in {
    "1", "true", "yes", "on"
}
DATASET_FILE = Path(
    os.getenv("STYLE_EXAMPLES_FILE", os.path.join(BASE_DIR, "data", "approved_intent_examples.jsonl"))
)
BOOTCAMP_OPENINGS_FILE = Path(
    os.getenv("BOOTCAMP_OPENINGS_FILE", os.path.join(BASE_DIR, "bootcamp_openings.jsonl"))
)
example_index = SMSExampleIndex(DATASET_FILE) if STYLE_EXAMPLES_ENABLED else None

AVAILABILITY_REPLY_POLICY = """Availability reply rule:
- A broad question such as “Are you free this afternoon?”, “Got time this evening?”, or “Are you around tonight?” needs a broad answer.
- If the calendar shows availability in that requested period, confirm it naturally and ask what time suits them. Example: “Yeah, I’m available this afternoon. What time suits you?”
- Do not answer a broad availability question by listing two or three arbitrary sample slots.
- Give exact times only when the client explicitly asks what times are available, proposes a specific time, or the requested period has very limited availability.
- Never invent availability. If the requested period is unavailable, say so briefly and offer the nearest genuine alternative.
- If availability exists, do not begin with “sorry”. End with one clear question such as “What time suits you?” or “What time were you after?” Never write “Where were you after?”
- Never offer a date or time that is already in the past."""

SMS_TYPOGRAPHY_POLICY = """SMS typography rule:
- Never use an em dash (—) or en dash (–). Use a comma, full stop, or ordinary hyphen instead."""


def render_style_examples(
    examples: list[tuple[str, str]],
    business_variables: Dict[str, Any],
) -> list[tuple[str, str]]:
    """Render permitted business variables inside retrieved style examples and strip unresolved placeholders."""
    if not examples:
        return []

    vars_map = dict(business_variables)
    website_val = vars_map.get("website") or vars_map.get("booking_url")
    if website_val:
        vars_map["website"] = website_val
        vars_map["booking_url"] = website_val

    if "phone" not in vars_map and "business_phone" in vars_map:
        vars_map["phone"] = vars_map["business_phone"]
    if "address" not in vars_map and "street_address" in vars_map:
        vars_map["address"] = vars_map["street_address"]

    rendered = []
    for incoming, reply in examples:
        r_inc = render_template_variables(incoming, vars_map)
        r_rep = render_template_variables(reply, vars_map)

        for token in CRITICAL_UNRENDERED_TOKENS:
            r_inc = re.sub(re.escape(token), "", r_inc, flags=re.IGNORECASE)
            r_rep = re.sub(re.escape(token), "", r_rep, flags=re.IGNORECASE)

        r_inc = re.sub(r"\{[a-z0-9_]+\}", "", r_inc, flags=re.IGNORECASE)
        r_rep = re.sub(r"\{[a-z0-9_]+\}", "", r_rep, flags=re.IGNORECASE)

        r_inc = re.sub(r"\s+", " ", r_inc).strip()
        r_rep = re.sub(r"\s+", " ", r_rep).strip()

        rendered.append((r_inc, r_rep))
    return rendered


def get_style_examples(
    query: str,
    intent: Optional[str] = None,
    limit: int = 3,
    render_variables: bool = True,
) -> list[tuple[str, str]]:
    """Return style examples matching query & intent within budget limits, with business variables rendered."""
    if not STYLE_EXAMPLES_ENABLED or example_index is None:
        return []
    if intent is None:
        intent = classify_query_intent(query)
    raw_examples = example_index.search(query, intent=intent, limit=limit)
    if render_variables:
        return render_style_examples(raw_examples, get_business_variable_values())
    return raw_examples


def build_model_instructions(
    system_prompt: str,
    examples: list[tuple[str, str]],
    style_profile: Optional[dict[str, Any]] = None,
) -> str:
    """Combine the stable prompt with examples and an optional style overlay, validating zero unresolved placeholders."""
    sections = [system_prompt, AVAILABILITY_REPLY_POLICY, SMS_TYPOGRAPHY_POLICY]
    if style_profile is not None:
        sections.append(render_style_profile(style_profile))
    if examples:
        style_text = "\n\n".join(
            f"Example {index + 1}\nIncoming: {incoming}\nNatural reply: {reply}"
            for index, (incoming, reply) in enumerate(examples)
        )
        sections.append(
            "Use these examples only for conversational rhythm. Never copy their facts, "
            f"names, links, or times.\n{style_text}"
        )

    instructions = "\n\n".join(sections)
    validate_no_unresolved_placeholders(instructions, context_label="model instructions")
    return instructions


def build_model_input(
    history_messages: list[Any],
    current_history_text: str,
    enriched_current_prompt: str,
    history_limit: int = 12,
) -> list[dict[str, str]]:
    """Map stored chat history and replace the latest inbound text with its context, validating zero unresolved placeholders."""
    selected = list(history_messages[-history_limit:])
    current_index = None
    for index in range(len(selected) - 1, -1, -1):
        message = selected[index]
        role = getattr(message, "role", None)
        text = getattr(message, "text", "")
        if role == "customer" and text == current_history_text:
            current_index = index
            break

    model_input: list[dict[str, str]] = []
    for index, message in enumerate(selected):
        role = getattr(message, "role", None)
        if role == "customer":
            api_role = "user"
        elif role in ("agent", "system", "draft"):
            api_role = "assistant"
        else:
            continue

        content = (
            enriched_current_prompt
            if index == current_index
            else str(getattr(message, "text", ""))
        )
        model_input.append({"role": api_role, "content": content})

    if current_index is None:
        model_input.append({"role": "user", "content": enriched_current_prompt})

    for item in model_input:
        validate_no_unresolved_placeholders(item["content"], context_label=f"input role '{item['role']}'")

    return model_input


def assemble_safe_prompt(
    system_prompt_tmpl: str,
    user_prompt_tmpl: str,
    query: str,
    retrieved_context: str,
    slots_str: str,
    now_local: datetime,
    style_profile: Optional[dict] = None,
) -> tuple[str, str, list[tuple[str, str]]]:
    """
    Execute full 8-step prompt assembly pipeline order:
    (1) System prompt -> (2) Business variables -> (3) Classify intent
    -> (4) Retrieve approved examples -> (5) Render permitted business variables inside retrieved style examples
    -> (6) Validate zero unresolved placeholders -> (7) Budget limit -> (8) Submit to LLM
    """
    business_variables = get_business_variable_values()
    business_variables["current_time"] = now_local.strftime("%A %d %B %Y, %I:%M %p %Z")

    system_prompt_rendered = render_template_variables(system_prompt_tmpl, business_variables)
    user_prompt_rendered = render_template_variables(user_prompt_tmpl, {
        **business_variables,
        "message": query,
        "knowledge": retrieved_context,
        "slots": slots_str,
    })

    intent = classify_query_intent(query)
    rendered_examples = get_style_examples(query, intent=intent, limit=3, render_variables=True)

    instructions = build_model_instructions(
        system_prompt_rendered,
        rendered_examples,
        style_profile or STYLE_PROFILE_STORE.get_applied(),
    )

    validate_no_unresolved_placeholders(instructions, context_label="system instructions")
    validate_no_unresolved_placeholders(user_prompt_rendered, context_label="user prompt")

    return instructions, user_prompt_rendered, rendered_examples

# Database configuration & persistence path resolution
# Mount check: use /data if it is mounted as a Fly.io volume, or fallback to local BASE_DIR
PERSIST_DIR = "/data" if os.path.exists("/data") else BASE_DIR

DATA_DIR = os.path.join(PERSIST_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

KNOWLEDGE_DIR = os.path.join(PERSIST_DIR, "knowledge")
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

STYLE_PROFILE_STORE = StyleProfileStore(DATA_DIR)
BOOTCAMP_STORE = BootcampStore(os.path.join(PERSIST_DIR, "bootcamp.db"))

PROMPTS_DIR = os.path.join(PERSIST_DIR, "prompts")
os.makedirs(PROMPTS_DIR, exist_ok=True)

# Copy default templates and data files if migrating to mounted persistent volume /data
if PERSIST_DIR == "/data":
    src_db = os.path.join(BASE_DIR, "assistant.db")
    dest_db = os.path.join(PERSIST_DIR, "assistant.db")
    if os.path.exists(src_db) and (not os.path.exists(dest_db) or os.path.getsize(dest_db) < 100):
        try:
            shutil.copy2(src_db, dest_db)
            print("[Volume Migration] Copied seed assistant.db to /data/assistant.db")
        except Exception as e:
            print(f"[Volume Migration] Failed to copy assistant.db: {e}")

    src_bootcamp_db = os.path.join(BASE_DIR, "bootcamp.db")
    dest_bootcamp_db = os.path.join(PERSIST_DIR, "bootcamp.db")
    if os.path.exists(src_bootcamp_db) and (not os.path.exists(dest_bootcamp_db) or os.path.getsize(dest_bootcamp_db) < 100):
        try:
            shutil.copy2(src_bootcamp_db, dest_bootcamp_db)
            print("[Volume Migration] Copied seed bootcamp.db to /data/bootcamp.db")
        except Exception as e:
            print(f"[Volume Migration] Failed to copy bootcamp.db: {e}")
    # Copy prompts
    src_prompts = os.path.join(BASE_DIR, "prompts")
    if os.path.exists(src_prompts):
        for item in os.listdir(src_prompts):
            src_file = os.path.join(src_prompts, item)
            dest_file = os.path.join(PROMPTS_DIR, item)
            if os.path.isfile(src_file) and not os.path.exists(dest_file):
                try:
                    shutil.copy2(src_file, dest_file)
                except Exception as e:
                    print(f"Failed to copy prompt default {item}: {e}")
    # Copy data configurations (like services.json)
    src_data = os.path.join(BASE_DIR, "data")
    if os.path.exists(src_data):
        for item in os.listdir(src_data):
            src_file = os.path.join(src_data, item)
            dest_file = os.path.join(DATA_DIR, item)
            if os.path.isfile(src_file) and not os.path.exists(dest_file):
                try:
                    shutil.copy2(src_file, dest_file)
                except Exception as e:
                    print(f"Failed to copy data default {item}: {e}")

DB_FILE = os.path.join(PERSIST_DIR, "assistant.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_FILE}")


engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Enable SQLite foreign keys
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

# SQLAlchemy Models
class Thread(Base):
    __tablename__ = "threads"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_phone = Column(String, unique=True, nullable=False, index=True)
    state = Column(String, default="auto-reply", nullable=False)  # auto-reply | needs-review | taken-over | escalated | resolved
    priority = Column(String, default="medium", nullable=False)  # low | medium | high
    assigned_agent_id = Column(String, nullable=True)
    sla_due_at = Column(DateTime, nullable=False)
    unread_count = Column(Integer, default=0, nullable=False)
    auto_reply_enabled = Column(Boolean, default=True, nullable=False)
    pending_slots = Column(Text, nullable=True) # JSON list of slots presented
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    messages = relationship("Message", back_populates="thread", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="thread", cascade="all, delete-orphan")
    events = relationship("ThreadEvent", back_populates="thread", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # customer | agent | system
    text = Column(Text, nullable=False)
    provider_message_id = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    thread = relationship("Thread", back_populates="messages")

class InboundWebhookReceipt(Base):
    """Atomic claim preventing a provider webhook from being processed twice."""
    __tablename__ = "inbound_webhook_receipts"

    provider_message_id = Column(String, primary_key=True)
    from_phone = Column(String, nullable=True)
    received_at = Column(DateTime, nullable=False)
    claimed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class Note(Base):
    __tablename__ = "notes"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    thread = relationship("Thread", back_populates="notes")

class ThreadEvent(Base):
    __tablename__ = "thread_events"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String, nullable=False)
    agent_id = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
    meta = Column(Text, nullable=True)
    
    thread = relationship("Thread", back_populates="events")


def find_thread_by_phone(db: Session, phone: str) -> Optional[Thread]:
    """Find a Thread matching a customer's canonical phone number, deduplicating duplicate threads if present."""
    if not phone:
        return None
    target_canonical = canonical_phone_number(phone)
    if not target_canonical:
        return None

    threads = db.query(Thread).all()
    matching_threads = [
        t for t in threads
        if t.customer_phone and canonical_phone_number(t.customer_phone) == target_canonical
    ]

    if not matching_threads:
        return None

    if len(matching_threads) == 1:
        t = matching_threads[0]
        if t.customer_phone != target_canonical:
            t.customer_phone = target_canonical
            db.commit()
        return t

    matching_threads.sort(
        key=lambda t: (
            1 if t.state != "resolved" else 0,
            len(t.messages) if getattr(t, "messages", None) else 0,
        ),
        reverse=True
    )
    primary = matching_threads[0]

    for duplicate in matching_threads[1:]:
        for child_model in (Message, Note, ThreadEvent):
            duplicate_children = (
                db.query(child_model)
                .filter(child_model.thread_id == duplicate.id)
                .all()
            )
            for child in duplicate_children:
                child.thread = primary
        db.delete(duplicate)

    # Remove duplicate canonical values before assigning the survivor's value.
    db.flush()
    primary.customer_phone = target_canonical
    db.commit()
    return primary

class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    summary = Column(String, nullable=False)
    customer_phone = Column(String, nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, default="scheduled", nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

def init_db():
    Base.metadata.create_all(bind=engine)
    
    # Auto-migration for SQLite columns on calendar_events
    with engine.connect() as conn:
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(calendar_events)").fetchall()
            col_names = [row[1] for row in result]
            if "status" not in col_names:
                conn.exec_driver_sql("ALTER TABLE calendar_events ADD COLUMN status VARCHAR DEFAULT 'scheduled'")
            if "notes" not in col_names:
                conn.exec_driver_sql("ALTER TABLE calendar_events ADD COLUMN notes TEXT")
            conn.commit()
        except Exception as e:
            print(f"Auto-migration info: {e}")

    # Seed sample initial bookings & threads if empty
    db = SessionLocal()
    try:
        if db.query(CalendarEvent).count() == 0:
            now_dt = datetime.utcnow()
            today_9am = now_dt.replace(hour=9, minute=0, second=0, microsecond=0)
            today_11am = now_dt.replace(hour=11, minute=30, second=0, microsecond=0)
            today_2pm = now_dt.replace(hour=14, minute=0, second=0, microsecond=0)
            
            sample_bookings = [
                CalendarEvent(
                    id=str(uuid.uuid4()),
                    summary="Full Body Relaxation Massage",
                    customer_phone="+61412345678",
                    start_time=today_9am,
                    end_time=today_9am + timedelta(minutes=60),
                    status="scheduled",
                    notes="Client requested essential oils."
                ),
                CalendarEvent(
                    id=str(uuid.uuid4()),
                    summary="Deep Tissue Massage & Consultation",
                    customer_phone="+61498765432",
                    start_time=today_11am,
                    end_time=today_11am + timedelta(minutes=45),
                    status="scheduled",
                    notes="First time client, lower back pain."
                ),
                CalendarEvent(
                    id=str(uuid.uuid4()),
                    summary="Nuru & Scalp Care Session",
                    customer_phone="+61455512345",
                    start_time=today_2pm,
                    end_time=today_2pm + timedelta(minutes=90),
                    status="scheduled",
                    notes="Prefers quiet session."
                )
            ]
            db.add_all(sample_bookings)
            
        if db.query(Thread).count() == 0:
            sample_thread = Thread(
                id=str(uuid.uuid4()),
                customer_phone="+61412345678",
                state="auto-reply",
                priority="medium",
                sla_due_at=datetime.utcnow() + timedelta(hours=2),
                unread_count=0,
                auto_reply_enabled=True
            )
            db.add(sample_thread)
            sample_msg = Message(
                id=str(uuid.uuid4()),
                thread_id=sample_thread.id,
                role="customer",
                text="Hi! I would like to book a relaxation massage session for today please.",
                at=datetime.utcnow()
            )
            db.add(sample_msg)

        db.commit()
        print("[Database Init] Successfully created and initialized SQLite tables and seed data.")
    except Exception as e:
        print(f"[Database Init Error]: {e}")
    finally:
        db.close()

init_db()

def load_knowledge_base():
    global KNOWLEDGE_CHUNKS
    KNOWLEDGE_CHUNKS = []
    knowledge_dir = KNOWLEDGE_DIR
    if not os.path.exists(knowledge_dir):
        os.makedirs(knowledge_dir, exist_ok=True)
        return
        
    for filename in os.listdir(knowledge_dir):
        filepath = os.path.join(knowledge_dir, filename)
        if not os.path.isfile(filepath):
            continue
            
        if filename.endswith(".txt"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
                    for chunk in chunks:
                        KNOWLEDGE_CHUNKS.append({
                            "source": filename,
                            "type": "text",
                            "text": chunk
                        })
            except Exception as e:
                print(f"Error reading txt file {filename}: {e}")
                
        elif filename.endswith(".jsonl"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                            
                            # Differentiate training few-shot examples
                            if "input" in obj and "output" in obj:
                                KNOWLEDGE_CHUNKS.append({
                                    "source": filename,
                                    "type": "few_shot",
                                    "input": str(obj["input"]).strip(),
                                    "output": str(obj["output"]).strip(),
                                    "text": f"Input: {obj['input']}\nOutput: {obj['output']}"
                                })
                            else:
                                text_val = None
                                for key in ["text", "content", "question", "answer", "body"]:
                                    if key in obj:
                                        text_val = str(obj[key])
                                        break
                                if not text_val:
                                    text_val = " ".join(str(val) for val in obj.values() if isinstance(val, (str, int, float)))
                                if text_val:
                                    KNOWLEDGE_CHUNKS.append({
                                        "source": filename,
                                        "type": "text",
                                        "text": text_val.strip()
                                    })
                        except Exception as line_e:
                            print(f"Error parsing jsonl line: {line_e}")
            except Exception as e:
                print(f"Error reading jsonl file {filename}: {e}")
                
    print(f"Loaded {len(KNOWLEDGE_CHUNKS)} knowledge chunks.")

load_knowledge_base()

def retrieve_knowledge_chunks(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    if not KNOWLEDGE_CHUNKS:
        return []
        
    query_words = [w.strip().lower() for w in query.split() if len(w.strip()) > 1]
    if not query_words:
        return KNOWLEDGE_CHUNKS[:limit]
        
    scored_chunks = []
    for chunk in KNOWLEDGE_CHUNKS:
        text_lower = chunk["text"].lower()
        score = sum(1 for word in query_words if word in text_lower)
        scored_chunks.append((score, chunk))
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    return [chunk for score, chunk in scored_chunks[:limit]]

def search_knowledge(query: str, limit: int = 5) -> str:
    results = retrieve_knowledge_chunks(query, limit)
    text_results = [r for r in results if r.get("type", "text") == "text"]
    
    if not text_results:
        text_results = results
        
    output_parts = []
    for res in text_results[:limit]:
        output_parts.append(f"[Source: {res['source']}]\n{res['text']}")
    return "\n\n".join(output_parts)


def get_live_services_context() -> str:
    """Read the current Settings service catalogue for every AI reply.

    This intentionally avoids a cache: saving Settings should affect the very next
    conversation without a restart or a separate knowledge-base upload.
    """
    services_path = os.path.join(DATA_DIR, "services.json")
    if not os.path.exists(services_path):
        return ""

    try:
        with open(services_path, "r", encoding="utf-8") as handle:
            services = json.load(handle)
    except Exception as exc:
        print(f"Failed to load live services for AI context: {exc}")
        return ""

    if not isinstance(services, list) or not services:
        return ""

    rendered = ["[Live services and prices from Settings]"]
    for service in services:
        if not isinstance(service, dict):
            continue
        name = str(service.get("name", "")).strip()
        if not name:
            continue
        details = [name]
        price = service.get("price")
        if price is not None:
            details.append(f"Price: ${price}")
        duration = service.get("duration")
        if duration is not None:
            details.append(f"Duration: {duration} minutes")
        description = re.sub(
            r"\s+", " ", str(service.get("description", "")).strip()
        )
        if description:
            details.append(f"Description: {description}")
        rendered.append("\n".join(details))
    return "\n\n".join(rendered) if len(rendered) > 1 else ""


BUSINESS_VARIABLES_PATH = os.path.join(DATA_DIR, "business_variables.json")
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
RESERVED_TEMPLATE_VARIABLES = {
    "message", "knowledge", "slots", "current_time", "name", "service", "time"
}
TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")


def load_business_variables() -> List[Dict[str, Any]]:
    default_meta = {d["key"]: d for d in BUSINESS_VARIABLE_DEFAULTS}
    if not os.path.exists(BUSINESS_VARIABLES_PATH):
        raw_items = [dict(item) for item in BUSINESS_VARIABLE_DEFAULTS]
    else:
        try:
            with open(BUSINESS_VARIABLES_PATH, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            if not isinstance(saved, list):
                raw_items = [dict(item) for item in BUSINESS_VARIABLE_DEFAULTS]
            else:
                raw_items = saved
        except Exception as exc:
            print(f"Failed to load business variables: {exc}")
            raw_items = [dict(item) for item in BUSINESS_VARIABLE_DEFAULTS]

    variables = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in seen:
            continue
        seen.add(key)

        meta = default_meta.get(key, {})
        label = str(item.get("label") or meta.get("label") or key.replace("_", " ").title()).strip()
        value = str(item.get("value", "")).strip()
        description = str(item.get("description") or meta.get("description") or f"Business detail for {label}").strip()
        is_required = bool(item.get("required") if "required" in item else meta.get("required", False))

        variables.append({
            "key": key,
            "token": f"{{{key}}}",
            "label": label,
            "value": value,
            "description": description,
            "required": is_required,
            "required_status": "required" if is_required else "optional",
        })
    return variables


def get_business_variable_values() -> Dict[str, str]:
    raw_vals = {
        item["key"]: item["value"]
        for item in load_business_variables()
        if item.get("value", "").strip()
    }
    website_val = raw_vals.get("website", "").strip() or raw_vals.get("booking_url", "").strip()
    if website_val:
        raw_vals["website"] = website_val
        raw_vals["booking_url"] = website_val

    if not os.path.exists(BUSINESS_VARIABLES_PATH) and not raw_vals:
        fallbacks = {
            "provider_name": "Tori",
            "suburb": "Melbourne",
            "website": "https://assistant-ui-hub.fly.dev/",
            "booking_url": "https://assistant-ui-hub.fly.dev/",
        }
        for k, v in fallbacks.items():
            if k not in raw_vals:
                raw_vals[k] = v

    return raw_vals



def render_template_variables(template: str, values: Dict[str, Any]) -> str:
    """Replace known {variable} tokens while leaving unknown tokens visible."""
    normalized = {key: str(value) for key, value in values.items() if value is not None}
    website_val = normalized.get("website") or normalized.get("booking_url")
    if website_val:
        normalized["website"] = website_val
        normalized["booking_url"] = website_val

    if "phone" not in normalized and "business_phone" in normalized:
        normalized["phone"] = normalized["business_phone"]
    if "address" not in normalized and "street_address" in normalized:
        normalized["address"] = normalized["street_address"]

    return TEMPLATE_VARIABLE_PATTERN.sub(
        lambda match: normalized.get(match.group(1), match.group(0)),
        template,
    )


def get_live_business_variables_context() -> str:
    variables = [item for item in load_business_variables() if item.get("value", "").strip()]
    if not variables:
        return ""
    rendered = ["[Authoritative business details from Settings]"]
    rendered.extend(f"{item['label']}: {item['value']}" for item in variables)
    return "\n".join(rendered)


def build_business_context(query: str, limit: int = 3) -> str:
    """Combine optional uploaded knowledge with authoritative live Settings."""
    output_parts = []
    matched_chunks = retrieve_knowledge_chunks(query, limit=limit)
    for result in matched_chunks:
        if result.get("type", "text") == "text":
            output_parts.append(f"[Source: {result['source']}]\n{result['text']}")

    variables_context = get_live_business_variables_context()
    if variables_context:
        output_parts.append(variables_context)

    services_context = get_live_services_context()
    if services_context:
        output_parts.append(services_context)
    return "\n\n".join(output_parts) or "No relevant business records found."


LEARNED_INFORMATION_FILENAME = "learned_information.jsonl"


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object.")
    return parsed


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
            **get_business_variable_values(),
            "current_time": current_business_time().strftime("%A %d %B %Y, %I:%M %p %Z"),
        }),
        get_style_examples(customer_message.text),
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
    try:
        result = _parse_json_object(response.output_text or "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The AI could not format the supplied information. Nothing was sent.") from exc

    customer_reply = sanitize_outgoing_urls(str(result.get("customer_reply", "")).strip())
    knowledge_summary = str(result.get("knowledge_summary", "")).strip()
    if not customer_reply or not knowledge_summary:
        raise HTTPException(status_code=502, detail="The AI returned an incomplete answer. Nothing was sent.")
    return {"customer_reply": customer_reply, "knowledge_summary": knowledge_summary}


def save_learned_information(
    request_event_id: str,
    customer_question: str,
    supplied_information: str,
    knowledge_summary: str,
) -> str:
    """Atomically upsert one reusable learned-information record and refresh RAG."""
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    filepath = os.path.join(KNOWLEDGE_DIR, LEARNED_INFORMATION_FILENAME)
    entry = {
        "id": request_event_id,
        "type": "information_request_resolution",
        "question": customer_question.strip(),
        "owner_information": supplied_information.strip(),
        "text": knowledge_summary.strip(),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    retained_lines = []
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    retained_lines.append(line)
                    continue
                if not isinstance(existing, dict) or existing.get("id") != request_event_id:
                    retained_lines.append(line)
    retained_lines.append(json.dumps(entry, ensure_ascii=False))
    temp_path = f"{filepath}.{uuid.uuid4().hex}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(retained_lines) + "\n")
    os.replace(temp_path, filepath)
    load_knowledge_base()
    return LEARNED_INFORMATION_FILENAME


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

# Google Calendar Service
class GoogleCalendarService:
    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory
        self.service = None
        self._cache = {}
        scopes = ["https://www.googleapis.com/auth/calendar"]

        service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if service_account_json and GOOGLE_LIBS_AVAILABLE:
            try:
                credential_info = json.loads(service_account_json)
                creds = service_account.Credentials.from_service_account_info(
                    credential_info,
                    scopes=scopes,
                )
                self.service = build("calendar", "v3", credentials=creds)
                print("Google Calendar API initialized from encrypted environment credentials.")
                return
            except Exception as e:
                print(f"Failed to initialize Google Calendar API from environment: {e}.")
        
        paths_to_check = [
            os.path.join(BASE_DIR, "service_account.json"),
            os.path.join(BASE_DIR, "credentials.json"),
            "./service_account.json",
            "./credentials.json"
        ]
        
        cred_path = None
        for path in paths_to_check:
            if os.path.exists(path):
                cred_path = path
                break
                
        if cred_path and GOOGLE_LIBS_AVAILABLE:
            try:
                creds = service_account.Credentials.from_service_account_file(cred_path, scopes=scopes)
                self.service = build("calendar", "v3", credentials=creds)
                print(f"Google Calendar API initialized with: {cred_path}")
            except Exception as e:
                print(f"Failed to initialize Google Calendar API: {e}. Falling back to SQLite.")
        else:
            print("Google Calendar credentials not found. Falling back to local SQLite database-backed calendar.")
            
    def get_busy_slots(self, start: datetime, end: datetime) -> List[Dict[str, datetime]]:
        import time
        from zoneinfo import ZoneInfo
        tz_hobart = ZoneInfo("Australia/Hobart")
        
        # Ensure start and end are aware in Hobart timezone
        start_aware = start.astimezone(tz_hobart) if start.tzinfo is not None else start.replace(tzinfo=tz_hobart)
        end_aware = end.astimezone(tz_hobart) if end.tzinfo is not None else end.replace(tzinfo=tz_hobart)
        
        # Cache lookup
        cache_key = (start_aware.isoformat(), end_aware.isoformat())
        now_ts = time.time()
        if hasattr(self, "_cache") and cache_key in self._cache:
            cached_ts, cached_val = self._cache[cache_key]
            if now_ts - cached_ts < 20:  # 20 seconds cache TTL
                print("[Calendar Cache] Cache hit! Returning cached busy slots.")
                return cached_val

        # Fetch and parse busy slots
        parsed_busy = []
        google_success = False
        if self.service:
            try:
                calendar_id = os.getenv("CALENDAR_ID", "primary")
                body = {
                    "timeMin": start_aware.isoformat(),
                    "timeMax": end_aware.isoformat(),
                    "items": [{"id": calendar_id}]
                }
                res = self.service.freebusy().query(body=body).execute()
                busy_list = res.get("calendars", {}).get(calendar_id, {}).get("busy", [])
                
                for b in busy_list:
                    b_start = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tz_hobart)
                    b_end = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tz_hobart)
                    parsed_busy.append({"start": b_start, "end": b_end})
                google_success = True
            except Exception as e:
                print(f"Error querying Google Calendar freebusy: {e}. Falling back to SQLite.")
                
        if not google_success:
            db = self.db_session_factory()
            try:
                start_naive = start_aware.replace(tzinfo=None)
                end_naive = end_aware.replace(tzinfo=None)
                events = db.query(CalendarEvent).filter(
                    (CalendarEvent.start_time < end_naive) & (CalendarEvent.end_time > start_naive)
                ).all()
                parsed_busy = [{"start": e.start_time.replace(tzinfo=tz_hobart), "end": e.end_time.replace(tzinfo=tz_hobart)} for e in events]
            finally:
                db.close()

        # Cache saving
        if hasattr(self, "_cache"):
            self._cache[cache_key] = (now_ts, parsed_busy)
        return parsed_busy
            
    def create_booking(self, summary: str, start: datetime, end: datetime, customer_phone: str) -> bool:
        # Clear cache on modification
        if hasattr(self, "_cache"):
            self._cache.clear()

        from zoneinfo import ZoneInfo
        tz_hobart = ZoneInfo("Australia/Hobart")
        
        # Ensure start and end are aware in Hobart timezone
        start_aware = start.astimezone(tz_hobart) if start.tzinfo is not None else start.replace(tzinfo=tz_hobart)
        end_aware = end.astimezone(tz_hobart) if end.tzinfo is not None else end.replace(tzinfo=tz_hobart)
        
        if self.service:
            try:
                calendar_id = os.getenv("CALENDAR_ID", "primary")
                event_body = {
                    "summary": summary,
                    "description": f"Customer phone: {customer_phone}",
                    "start": {
                        "dateTime": start_aware.isoformat(),
                    },
                    "end": {
                        "dateTime": end_aware.isoformat(),
                    }
                }
                self.service.events().insert(calendarId=calendar_id, body=event_body).execute()
                return True
            except Exception as e:
                print(f"Error creating Google Calendar booking: {e}. Falling back to SQLite.")
                
        db = self.db_session_factory()
        try:
            booking = CalendarEvent(
                id=str(uuid.uuid4()),
                customer_phone=customer_phone,
                summary=summary,
                start_time=start_aware.replace(tzinfo=None),
                end_time=end_aware.replace(tzinfo=None)
            )
            db.add(booking)
            db.commit()
            return True
        except Exception as e:
            print(f"Failed to create booking in SQLite: {e}")
            return False
        finally:
            db.close()

    def delete_booking(self, booking_id: str) -> bool:
        # Clear cache on modification
        if hasattr(self, "_cache"):
            self._cache.clear()

        deleted_gc = False
        if self.service:
            try:
                calendar_id = os.getenv("CALENDAR_ID", "primary")
                self.service.events().delete(calendarId=calendar_id, eventId=booking_id).execute()
                deleted_gc = True
            except Exception as e:
                print(f"Error deleting Google Calendar booking {booking_id}: {e}.")
        
        db = self.db_session_factory()
        try:
            booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()
            if booking:
                db.delete(booking)
                db.commit()
                return True
            return deleted_gc
        except Exception as e:
            print(f"Failed to delete booking {booking_id} in SQLite: {e}")
            return False
        finally:
            db.close()


calendar_service = GoogleCalendarService(SessionLocal)

# Initialize OpenAI Client
openai_client = None
if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
    try:
        openai_client = OpenAI()
        print("OpenAI client successfully initialized.")
    except Exception as e:
        print(f"OpenAI client initialization failed: {e}")

# Global flag to enable/disable auto-replies
AUTO_REPLY_GLOBAL_ENABLED = True
auto_reply_path = os.path.join(DATA_DIR, "auto_reply_global.json")
if os.path.exists(auto_reply_path):
    try:
        with open(auto_reply_path, "r", encoding="utf-8") as f:
            AUTO_REPLY_GLOBAL_ENABLED = json.load(f).get("enabled", True)
    except Exception:
        pass

# Global training mode flag
TRAINING_MODE_ENABLED = False
training_mode_path = os.path.join(DATA_DIR, "training_mode.json")
if os.path.exists(training_mode_path):
    try:
        with open(training_mode_path, "r", encoding="utf-8") as f:
            TRAINING_MODE_ENABLED = json.load(f).get("enabled", False)
    except Exception:
        pass

def match_qa_rule(message_text: str) -> Optional[str]:
    if not message_text:
        return None
    qa_path = os.path.join(DATA_DIR, "qa_rules.json")
    if os.path.exists(qa_path):
        try:
            with open(qa_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
                if isinstance(rules, list):
                    for rule in rules:
                        trigger = rule.get("trigger", "").strip().lower()
                        reply = rule.get("reply", "")
                        if trigger and trigger in message_text.lower():
                            return reply
        except Exception as e:
            print(f"Failed to read QA rules: {e}")
    return None

FIRST_CONTACT_AUTORESPONDER_PATH = os.path.join(DATA_DIR, "first_contact_autoresponder.json")
FIRST_CONTACT_AUTORESPONDER_DEFAULT = {
    "enabled": False,
    "cooldownDays": 30,
    "delaySeconds": 0,
    "message": "",
}

def load_first_contact_autoresponder() -> Dict[str, Any]:
    config = dict(FIRST_CONTACT_AUTORESPONDER_DEFAULT)
    if os.path.exists(FIRST_CONTACT_AUTORESPONDER_PATH):
        try:
            with open(FIRST_CONTACT_AUTORESPONDER_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                config.update(saved)
        except Exception as e:
            print(f"Failed to read first-contact auto-responder settings: {e}")
    try:
        config["cooldownDays"] = max(1, min(3650, int(config.get("cooldownDays", 30))))
    except (TypeError, ValueError):
        config["cooldownDays"] = 30
    try:
        config["delaySeconds"] = max(0, min(3600, int(config.get("delaySeconds", 0))))
    except (TypeError, ValueError):
        config["delaySeconds"] = 0
    config["enabled"] = bool(config.get("enabled", False))
    config["message"] = str(config.get("message", "")).strip()
    return config

# Pydantic Schemas for Requests
class WebhookSMSInput(BaseModel):
    from_phone: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    body: Optional[str] = None
    providerMessageId: Optional[str] = None
    receivedAt: Optional[datetime] = None
    isSimulation: bool = False

    @model_validator(mode='before')
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "from" not in data and "sender" in data:
                data["from"] = data["sender"]
            if "body" not in data and "message" in data:
                data["body"] = data["message"]
            if "receivedAt" not in data:
                if "received_at" in data:
                    data["receivedAt"] = data["received_at"]
                else:
                    data["receivedAt"] = datetime.utcnow().isoformat() + "Z"
            if "providerMessageId" not in data:
                data["providerMessageId"] = data.get("message_id") or data.get("original_message_id")
        return data

    class Config:
        populate_by_name = True

class TakeoverInput(BaseModel):
    agentId: str

class ReplyInput(BaseModel):
    agentId: str
    text: str = Field(min_length=1, max_length=1600)
    clientRequestId: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def clean_reply(self):
        self.text = self.text.strip()
        self.clientRequestId = (self.clientRequestId or "").strip() or None
        if not self.text:
            raise ValueError("Reply text is required.")
        return self

class NoteInput(BaseModel):
    agentId: str
    text: str

class EscalateInput(BaseModel):
    agentId: str
    reason: str

class ResolveInput(BaseModel):
    agentId: str
    resolution: str

class AutoresponderInput(BaseModel):
    enabled: bool

class FirstContactAutoresponderInput(BaseModel):
    enabled: bool = False
    cooldownDays: int = Field(default=30, ge=1, le=3650)
    delaySeconds: int = Field(default=0, ge=0, le=3600)
    message: str = Field(default="", max_length=1600)

    @model_validator(mode="after")
    def require_message_when_enabled(self):
        self.message = self.message.strip()
        if self.enabled and not self.message:
            raise ValueError("A reply message is required when the first-contact auto-responder is enabled.")
        return self


class InformationRequestResponseInput(BaseModel):
    agentId: str = Field(default="user", min_length=1, max_length=100)
    information: str = Field(min_length=1, max_length=6000)
    requestEventId: Optional[str] = None

    @model_validator(mode="after")
    def clean_information(self):
        self.agentId = self.agentId.strip() or "user"
        self.information = self.information.strip()
        if not self.information:
            raise ValueError("Information is required.")
        return self


# FastAPI app setup
app = FastAPI(title="Assistant UI Backend")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUTH_USERNAME = os.getenv("APP_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("APP_PASSWORD", "")
PUBLIC_EXACT_PATHS = {
    "/",
    "/docs",
    "/openapi.json",
    "/api/health",
    "/booking",
    "/landing.html",
    "/manifest.json",
    "/sw.js",
    "/favicon.ico",
    "/webhooks/sms",
}


def is_public_request(request: Request) -> bool:
    """Keep the public website, booking widget, and required integrations open."""
    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()

    if path in PUBLIC_EXACT_PATHS:
        return True
    if path.startswith("/images/") or path.startswith("/assets/"):
        return True

    # These three routes are the customer-facing booking widget API only.
    if method == "GET" and path in {"/api/services", "/api/calendar/freebusy"}:
        return True
    if method == "POST" and path == "/api/calendar/bookings":
        return True

    return False


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    if is_public_request(request):
        return await call_next(request)

    if not AUTH_PASSWORD:
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    scheme, _, encoded = authorization.partition(" ")
    supplied_username = ""
    supplied_password = ""

    if scheme.lower() == "basic" and encoded:
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            supplied_username, separator, supplied_password = decoded.partition(":")
            if not separator:
                supplied_username = ""
                supplied_password = ""
        except (ValueError, UnicodeDecodeError):
            pass

    if (
        hmac.compare_digest(supplied_username, AUTH_USERNAME)
        and hmac.compare_digest(supplied_password, AUTH_PASSWORD)
    ):
        return await call_next(request)

    return Response(
        content="Authentication required.",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Assistant UI", charset="UTF-8"'},
    )


# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Helper function to format datetimes to UTC ISO strings ending in 'Z'
def format_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def current_business_time() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Australia/Hobart"))


def build_broad_availability_guidance(
    message: str,
    now_local: datetime,
    busy_slots: list[dict[str, datetime]],
    working_hours_by_day: dict[str, dict[str, Any]],
) -> str:
    """Summarise a broad time-of-day request without selecting arbitrary slots."""
    text = (message or "").lower()
    periods = (
        ("morning", 6, 12),
        ("afternoon", 12, 17),
        ("evening", 17, 24),
        ("tonight", 17, 24),
    )
    selected = next((period for period in periods if period[0] in text), None)
    if not selected:
        return ""

    label, start_hour, end_hour = selected
    target_date = (now_local + timedelta(days=1)).date() if "tomorrow" in text else now_local.date()
    period_start = datetime.combine(target_date, datetime.min.time(), tzinfo=now_local.tzinfo) + timedelta(hours=start_hour)
    period_end = datetime.combine(target_date, datetime.min.time(), tzinfo=now_local.tzinfo) + timedelta(hours=end_hour)
    earliest = now_local
    cursor = max(period_start, earliest)
    minutes = 15 * ((cursor.minute + 14) // 15)
    cursor = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)

    available_starts = 0
    while cursor < period_end:
        slot_end = cursor + timedelta(minutes=30)
        day_cfg = working_hours_by_day.get(DAY_NAMES[cursor.weekday()])
        if day_cfg and day_cfg.get("enabled", False):
            open_h, open_m = map(int, day_cfg["open"].split(":"))
            close_h, close_m = map(int, day_cfg["close"].split(":"))
            cursor_minutes = cursor.hour * 60 + cursor.minute
            end_minutes = slot_end.hour * 60 + slot_end.minute
            inside_hours = cursor_minutes >= open_h * 60 + open_m and end_minutes <= close_h * 60 + close_m
            overlaps = any(cursor < busy["end"] and slot_end > busy["start"] for busy in busy_slots)
            if inside_hours and not overlaps:
                available_starts += 1
        cursor += timedelta(minutes=15)

    day_label = "tomorrow" if target_date != now_local.date() else "today"
    if available_starts:
        return (
            f"Requested-period guidance: {label} {day_label} has availability. "
            "Confirm availability broadly and ask what time suits them. Do not list sample times."
        )
    return (
        f"Requested-period guidance: {label} {day_label} has no valid 30-minute opening. "
        "Do not claim availability in that period; respond briefly and offer a genuine alternative."
    )

# Customer-arrival alarms use a conservative deterministic fast path plus a
# structured AI tool call for messages whose meaning depends on context.
ARRIVAL_NEGATIVE_PATTERNS = (
    r"\bnot (?:there|here) yet\b",
    r"\b(?:have not|haven't|has not|hasn't) arrived\b",
    r"\b(?:when|once|before|after) (?:i|we) (?:arrive|get there)\b",
    r"\b(?:i|we)(?:'m| are| am)? (?:on (?:my|our|the) way|almost there)\b",
    r"\b(?:minutes?|mins?|hours?) away\b",
    r"\b(?:will|should|might|may) (?:be there|arrive)\b",
)
ARRIVAL_POSITIVE_PATTERNS = (
    r"\b(?:i(?:'m| am)|we(?:'re| are)) here\b",
    r"\b(?:i|we)(?:'ve| have)? (?:just )?arrived\b",
    r"\bjust (?:got|made it) here\b",
    r"\b(?:i(?:'m| am)|we(?:'re| are)) (?:at|outside) (?:the )?(?:front )?door\b",
    r"\b(?:i(?:'m| am)|we(?:'re| are)) in (?:the )?(?:waiting room|reception)\b",
    r"\bwaiting (?:outside|out front|downstairs|at (?:the )?(?:front )?door)\b",
    r"\b(?:parked|pulled up) (?:outside|out front)\b",
)


def is_clear_customer_arrival(message: str) -> bool:
    normalized = " ".join((message or "").casefold().replace("’", "'").split())
    if not normalized or any(re.search(pattern, normalized) for pattern in ARRIVAL_NEGATIVE_PATTERNS):
        return False
    return any(re.search(pattern, normalized) for pattern in ARRIVAL_POSITIVE_PATTERNS)


def record_customer_arrival_event(
    db: Session,
    thread: Thread,
    source_message_id: str,
    detection_method: str,
) -> bool:
    marker = source_message_id or ""
    existing_events = db.query(ThreadEvent).filter(
        ThreadEvent.thread_id == thread.id,
        ThreadEvent.type == "customer-arrived",
    ).all()
    for event in existing_events:
        try:
            event_meta = json.loads(event.meta or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if marker and event_meta.get("source_message_id") == marker:
            return False

    db.add(ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="customer-arrived",
        agent_id=None,
        meta=json.dumps({
            "source_message_id": marker or None,
            "detection_method": detection_method,
        }),
        at=datetime.utcnow(),
    ))
    return True


@app.post("/api/threads/{thread_id}/autoresponder")
def toggle_autoresponder(thread_id: str, payload: AutoresponderInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.auto_reply_enabled = payload.enabled
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="state-changed",
        agent_id=None,
        meta=json.dumps({"autoReplyEnabled": payload.enabled}),
        at=datetime.utcnow(),
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "autoReplyEnabled": thread.auto_reply_enabled}


@app.get("/api/calendar/bookings")
def get_bookings(db: Session = Depends(get_db)):
    from zoneinfo import ZoneInfo
    tz_hobart = ZoneInfo("Australia/Hobart")

    def format_booking_dt(dt: datetime) -> str:
        """Return an ISO timestamp with the real Hobart UTC offset."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz_hobart)
        return dt.astimezone(tz_hobart).isoformat()

    results = []
    
    if calendar_service.service:
        try:
            calendar_id = os.getenv("CALENDAR_ID", "primary")
            events_result = calendar_service.service.events().list(
                calendarId=calendar_id, orderBy='startTime', singleEvents=True
            ).execute()
            events = events_result.get('items', [])
            for e in events:
                start_raw = e["start"].get("dateTime", e["start"].get("date"))
                end_raw = e["end"].get("dateTime", e["end"].get("date"))
                
                # Parse as timezone-aware datetime
                b_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                
                # Convert to Hobart local time; the response formatter restores the explicit offset.
                b_start_local = b_start.astimezone(tz_hobart).replace(tzinfo=None)
                b_end_local = b_end.astimezone(tz_hobart).replace(tzinfo=None)
                
                desc = e.get("description", "")
                customer_phone = desc.replace("Customer phone: ", "") if "Customer phone: " in desc else None
                results.append({
                    "id": e.get("id"),
                    "customerPhone": customer_phone,
                    "summary": e.get("summary"),
                    "startTime": format_booking_dt(b_start_local),
                    "endTime": format_booking_dt(b_end_local),
                    "status": "scheduled",
                    "notes": desc
                })
        except Exception as ex:
            print(f"Error listing Google Calendar events: {ex}")
            
    db_events = db.query(CalendarEvent).order_by(CalendarEvent.start_time.asc()).all()
    for de in db_events:
        # de.start_time and de.end_time are naive local Hobart times in database.
        # Return them with an explicit Hobart offset so browsers preserve the booked time.
        de_start_str = format_booking_dt(de.start_time)
        if not any(r["startTime"] == de_start_str and r["customerPhone"] == de.customer_phone for r in results):
            results.append({
                "id": de.id,
                "customerPhone": de.customer_phone,
                "summary": de.summary,
                "startTime": de_start_str,
                "endTime": format_booking_dt(de.end_time),
                "status": getattr(de, "status", "scheduled") or "scheduled",
                "notes": getattr(de, "notes", "") or ""
            })
            
    return results


class UpdateBookingInput(BaseModel):
    summary: Optional[str] = None
    customerPhone: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


@app.put("/api/calendar/bookings/{booking_id}")
def update_booking_endpoint(booking_id: str, payload: UpdateBookingInput, db: Session = Depends(get_db)):
    from zoneinfo import ZoneInfo
    tz_hobart = ZoneInfo("Australia/Hobart")

    def format_booking_dt(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz_hobart)
        return dt.astimezone(tz_hobart).isoformat()

    booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()
    
    if not booking:
        dt_now = datetime.utcnow()
        booking = CalendarEvent(
            id=booking_id,
            summary=payload.summary or "Scheduled Appointment",
            customer_phone=payload.customerPhone,
            start_time=dt_now,
            end_time=dt_now + timedelta(minutes=30),
            status=payload.status or "scheduled",
            notes=payload.notes or ""
        )
        db.add(booking)

    if payload.summary is not None:
        booking.summary = payload.summary
    if payload.customerPhone is not None:
        booking.customer_phone = payload.customerPhone
    if payload.status is not None:
        booking.status = payload.status
    if payload.notes is not None:
        booking.notes = payload.notes
        
    if payload.startTime is not None:
        try:
            dt_start = datetime.fromisoformat(payload.startTime.replace("Z", "+00:00"))
            booking.start_time = dt_start.astimezone(tz_hobart).replace(tzinfo=None)
        except Exception:
            pass
            
    if payload.endTime is not None:
        try:
            dt_end = datetime.fromisoformat(payload.endTime.replace("Z", "+00:00"))
            booking.end_time = dt_end.astimezone(tz_hobart).replace(tzinfo=None)
        except Exception:
            pass

    db.commit()
    db.refresh(booking)

    if calendar_service.service:
        try:
            calendar_id = os.getenv("CALENDAR_ID", "primary")
            body = {}
            if payload.summary is not None:
                body["summary"] = payload.summary
            if payload.customerPhone is not None:
                body["description"] = f"Customer phone: {payload.customerPhone}"
            if payload.startTime is not None:
                body["start"] = {"dateTime": payload.startTime}
            if payload.endTime is not None:
                body["end"] = {"dateTime": payload.endTime}
            if body:
                calendar_service.service.events().patch(
                    calendarId=calendar_id, eventId=booking_id, body=body
                ).execute()
        except Exception as e:
            print(f"Google Calendar patch failed/skipped: {e}")

    return {
        "id": booking.id,
        "customerPhone": booking.customer_phone,
        "summary": booking.summary,
        "startTime": format_booking_dt(booking.start_time),
        "endTime": format_booking_dt(booking.end_time),
        "status": getattr(booking, "status", "scheduled") or "scheduled",
        "notes": getattr(booking, "notes", "") or ""
    }


@app.delete("/api/calendar/bookings/{booking_id}")
def delete_booking_endpoint(booking_id: str, db: Session = Depends(get_db)):
    success = calendar_service.delete_booking(booking_id)
    if not success:
        booking = db.query(CalendarEvent).filter(CalendarEvent.id == booking_id).first()
        if booking:
            db.delete(booking)
            db.commit()
            return {"status": "success"}
        raise HTTPException(status_code=404, detail="Booking not found or could not be deleted.")
    return {"status": "success"}


WORKING_HOURS_PATH = os.path.join(DATA_DIR, "working_hours.json")
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

def load_working_hours():
    if os.path.exists(WORKING_HOURS_PATH):
        try:
            with open(WORKING_HOURS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_WORKING_HOURS


@app.get("/api/calendar/freebusy")
def get_free_slots_endpoint(duration: int = Query(30), db: Session = Depends(get_db)):
    working_hours = load_working_hours()
    wh_by_day = {entry["day"]: entry for entry in working_hours}

    from zoneinfo import ZoneInfo
    tz_hobart = ZoneInfo("Australia/Hobart")

    now = datetime.now(tz_hobart)
    dt = now

    minutes = 15 * ((dt.minute + 14) // 15)
    dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)

    busy_slots = calendar_service.get_busy_slots(dt, dt + timedelta(days=14))
    free_slots = []
    limit_dt = dt + timedelta(days=14)

    while dt < limit_dt and len(free_slots) < 500:
        day_name = DAY_NAMES[dt.weekday()]
        day_cfg = wh_by_day.get(day_name)

        if day_cfg and day_cfg.get("enabled", False):
            open_h, open_m = map(int, day_cfg["open"].split(":"))
            close_h, close_m = map(int, day_cfg["close"].split(":"))
            open_mins = open_h * 60 + open_m
            close_mins = close_h * 60 + close_m
            dt_mins = dt.hour * 60 + dt.minute

            slot_end = dt + timedelta(minutes=duration)
            slot_end_mins = slot_end.hour * 60 + slot_end.minute

            if dt_mins >= open_mins and slot_end_mins <= close_mins:
                overlap = False
                for busy in busy_slots:
                    if dt < busy["end"] and slot_end > busy["start"]:
                        overlap = True
                        break
                if not overlap:
                    free_slots.append({
                        "startTime": format_dt(dt),
                        "endTime": format_dt(slot_end),
                    })
        dt += timedelta(minutes=15)

    return free_slots


# Endpoints

def run_sms_reply_logic(
    db: Session,
    thread_id: str,
    body: str,
    provider_message_id: str,
    received_at_naive: datetime,
    dispatch_sms: bool = True,
    draft_only: bool = False,
):
    import json
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return False, False
        
    booking_confirmed = False
    slots_presented = False
    clean_body = body.strip().lower()
    
    # Step 1: Read uploaded knowledge plus the authoritative live Settings catalogue.
    retrieved_context = build_business_context(body)
    
    now_local = current_business_time()
    reply_at_naive = datetime.utcnow()
    
    # Step 2: Query next 3 available slots using configured working hours
    dt = now_local + timedelta(hours=1)
    minutes = 15 * ((dt.minute + 14) // 15)
    dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)

    wh = load_working_hours()
    wh_by_day = {entry["day"]: entry for entry in wh}

    busy_slots = calendar_service.get_busy_slots(dt, dt + timedelta(days=14))
    free_slots = []
    limit_dt = dt + timedelta(days=14)
    while dt < limit_dt and len(free_slots) < 3:
        day_name = DAY_NAMES[dt.weekday()]
        day_cfg = wh_by_day.get(day_name)
        if day_cfg and day_cfg.get("enabled", False):
            open_h, open_m = map(int, day_cfg["open"].split(":"))
            close_h, close_m = map(int, day_cfg["close"].split(":"))
            open_mins = open_h * 60 + open_m
            close_mins = close_h * 60 + close_m
            dt_mins = dt.hour * 60 + dt.minute
            slot_end = dt + timedelta(minutes=30)
            slot_end_mins = slot_end.hour * 60 + slot_end.minute
            if dt_mins >= open_mins and slot_end_mins <= close_mins:
                overlap = False
                for b in busy_slots:
                    if dt < b["end"] and slot_end > b["start"]:
                        overlap = True
                        break
                if not overlap:
                    free_slots.append((dt, slot_end))
        dt += timedelta(minutes=15)

    slots_str = ""
    for i, (s, e) in enumerate(free_slots):
        slots_str += f"- Option {i+1}: {s.isoformat()} to {e.isoformat()}\n"
    if not slots_str:
        slots_str = "No openings available."
    broad_availability_guidance = build_broad_availability_guidance(
        body,
        now_local,
        busy_slots,
        wh_by_day,
    )
    if broad_availability_guidance:
        slots_str += f"\n{broad_availability_guidance}"

    # Populate pending_slots and slots_presented for booking intent
    scheduling_keywords = ["book", "schedule", "appointment", "free", "busy", "slot", "when"]
    has_scheduling_intent = any(kw in clean_body for kw in scheduling_keywords)
    if has_scheduling_intent and free_slots:
        slot_options = []
        for s, e in free_slots:
            slot_options.append({
                "start": s.isoformat(),
                "end": e.isoformat()
            })
        thread.pending_slots = json.dumps(slot_options)
        slots_presented = True

    # Step 3 & 4: Load prompts templates
    system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
    user_prompt_path = os.path.join(PROMPTS_DIR, "user_prompt.txt")
    
    system_prompt_tmpl = "You are a helpful, friendly customer service agent. Use the context and slots."
    if os.path.exists(system_prompt_path):
        with open(system_prompt_path, "r", encoding="utf-8") as f:
            system_prompt_tmpl = f.read()
            
    user_prompt_tmpl = "Customer message: {message}\nKnowledge context:\n{knowledge}\nCalendar openings:\n{slots}"
    if os.path.exists(user_prompt_path):
        with open(user_prompt_path, "r", encoding="utf-8") as f:
            user_prompt_tmpl = f.read()
            
    business_variables = get_business_variable_values()
    system_prompt_rendered = render_template_variables(system_prompt_tmpl, {
        **business_variables,
        "current_time": now_local.strftime("%A %d %B %Y, %I:%M %p %Z"),
    })
    user_prompt_rendered = render_template_variables(user_prompt_tmpl, {
        **business_variables,
        "message": body,
        "knowledge": retrieved_context,
        "slots": slots_str,
    })
    
    # Check Q&A Rules first
    assistant_reply = match_qa_rule(body)
    if assistant_reply:
        print(f"[QA Rules Match] Trigger matched. Using pre-configured reply.")
        
    # Check if booking confirmation number is received (e.g. "1", "2", "3") and there are pending slots
    elif not draft_only and clean_body in ("1", "2", "3") and thread.pending_slots:
        try:
            slots = json.loads(thread.pending_slots)
            index = int(clean_body) - 1
            if 0 <= index < len(slots):
                slot = slots[index]
                start_dt = datetime.fromisoformat(slot["start"])
                end_dt = datetime.fromisoformat(slot["end"])
                
                booking_success = calendar_service.create_booking(
                    summary="Appointment",
                    start=start_dt,
                    end=end_dt,
                    customer_phone=thread.customer_phone
                )
                if booking_success:
                    thread.pending_slots = None
                    booking_confirmed = True
                    assistant_reply = f"All booked for {start_dt.strftime('%A at %I:%M %p')}."
        except Exception as e:
            print(f"Booking confirmation failed: {e}")
    
    # Step 5: Chat completions via OpenAI Responses API if available
    elif openai_client:
        try:
            flat_tools = [{
                "type": "function",
                "name": "signal_customer_arrival",
                "description": (
                    "Signal that the customer explicitly says they are physically at the service "
                    "location now. Use only for a present, completed arrival. Do not use when they "
                    "are travelling, nearby, running late, discussing a future arrival, asking for "
                    "directions, or saying they have not arrived."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            }]
            
            examples = get_style_examples(body)
            instructions = build_model_instructions(
                system_prompt_rendered,
                examples,
                STYLE_PROFILE_STORE.get_applied(),
            )
            if draft_only:
                instructions += (
                    "\n\nCatch-up review: create a draft only. If the available conversation, "
                    "business context, or calendar does not support a confident answer, output "
                    "exactly [[HANDOFF: concise reason]] instead of a customer-facing holding message."
                )

            history_msgs = db.query(Message).filter(Message.thread_id == thread.id).order_by(Message.at.asc()).all()
            input_history = build_model_input(
                history_msgs,
                current_history_text=body,
                enriched_current_prompt=user_prompt_rendered,
            )

            response = openai_client.responses.create(
                model="gpt-5.6-terra",
                instructions=instructions,
                input=input_history,
                tools=flat_tools,
                store=False
            )
            
            tool_calls = [item for item in (response.output or []) if item.type == "function_call"]
            
            if tool_calls:
                input_history.extend(
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in (response.output or [])
                )

                source_message = None
                if provider_message_id:
                    source_message = db.query(Message).filter(
                        Message.thread_id == thread.id,
                        Message.role == "customer",
                        Message.provider_message_id == provider_message_id,
                    ).first()
                if not source_message:
                    source_message = db.query(Message).filter(
                        Message.thread_id == thread.id,
                        Message.role == "customer",
                        Message.text == body,
                        Message.at == received_at_naive,
                    ).first()
                source_message_id = source_message.id if source_message else (provider_message_id or "")
                
                for tool_call in tool_calls:
                    if tool_call.name == "make_calendar_booking":
                        args = json.loads(tool_call.arguments)
                        start_time_str = args.get("start_time")
                        summary = args.get("summary", "Appointment")
                        
                        slot_start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                        slot_end_dt = slot_start_dt + timedelta(minutes=30)
                        
                        booking_success = calendar_service.create_booking(
                            summary=summary,
                            start=slot_start_dt,
                            end=slot_end_dt,
                            customer_phone=thread.customer_phone
                        )
                        
                        if booking_success:
                            booking_confirmed = True
                            
                        tool_result = {"status": "success" if booking_success else "failed"}
                        input_history.append({
                            "type": "function_call_output",
                            "call_id": tool_call.call_id,
                            "output": json.dumps(tool_result),
                        })
                    elif tool_call.name == "signal_customer_arrival":
                        arrival_recorded = record_customer_arrival_event(
                            db,
                            thread,
                            source_message_id,
                            "ai",
                        )
                        input_history.append({
                            "type": "function_call_output",
                            "call_id": tool_call.call_id,
                            "output": json.dumps({
                                "status": "recorded" if arrival_recorded else "already-recorded"
                            }),
                        })
                        
                final_response = openai_client.responses.create(
                    model="gpt-5.6-terra",
                    instructions=instructions,
                    input=input_history,
                    store=False
                )
                assistant_reply = final_response.output_text
            else:
                assistant_reply = response.output_text
                
        except Exception as e:
            print(f"OpenAI error: {e}. Falling back to simulation.")
            assistant_reply = None
            
    # Step 6: Simulation / Mock Fallback if OpenAI call fails/unavailable
    if not assistant_reply and draft_only:
        thread.state = "needs-review"
        latest_customer_message = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "customer",
        ).order_by(Message.at.desc(), Message.id.desc()).first()
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="information-request",
            agent_id=None,
            meta=json.dumps({
                "reason": "AI response unavailable",
                "status": "pending",
                "customer_message_id": latest_customer_message.id if latest_customer_message else None,
            }),
            at=datetime.utcnow(),
        ))
        db.commit()
        return booking_confirmed, slots_presented

    if not assistant_reply:
        scheduling_keywords = ["book", "schedule", "appointment", "free", "busy", "slot", "when"]
        has_scheduling_intent = any(kw in clean_body for kw in scheduling_keywords)
        
        if has_scheduling_intent:
            if free_slots:
                slot_options = []
                readable_slots = []
                for i, (s, e) in enumerate(free_slots):
                    slot_options.append({
                        "start": s.isoformat(),
                        "end": e.isoformat()
                    })
                    readable_slots.append(f"{i+1}) {s.strftime('%a at %I:%M %p')}")
                assistant_reply = (
                    f"I've got {', '.join(readable_slots)}. Which one works for you?"
                )
                thread.pending_slots = json.dumps(slot_options)
                slots_presented = True
                
        if not assistant_reply:
            assistant_reply = "Hey, I've got your message but I can't check that properly right now. I'll get back to you shortly."
            
    assistant_reply = sanitize_outgoing_urls(assistant_reply)

    catch_up_handoff = (
        re.fullmatch(r"\s*\[\[HANDOFF(?::\s*(.*?))?\]\]\s*", assistant_reply or "", re.IGNORECASE)
        if draft_only else None
    )
    if catch_up_handoff:
        reason = (catch_up_handoff.group(1) or "Human guidance requested").strip()
        thread.state = "needs-review"
        latest_customer_message = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "customer",
        ).order_by(Message.at.desc(), Message.id.desc()).first()
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="information-request",
            agent_id=None,
            meta=json.dumps({
                "reason": reason,
                "status": "pending",
                "customer_message_id": latest_customer_message.id if latest_customer_message else None,
            }),
            at=datetime.utcnow(),
        ))
    elif TRAINING_MODE_ENABLED or draft_only:
        draft_message = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="draft",
            text=assistant_reply,
            at=reply_at_naive
        )
        db.add(draft_message)
        thread.state = "needs-review"
        
        event_log = ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="draft-created",
            agent_id=None,
            meta=json.dumps({
                "message_id": draft_message.id,
                **({"source": "catch-up"} if draft_only else {}),
            }),
            at=reply_at_naive,
        )
        db.add(event_log)
    else:
        # Store as sent only after the gateway accepts the SMS. On failure the
        # reply remains a visible draft for human retry/review.
        system_message = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="system",
            text=assistant_reply,
            at=reply_at_naive
        )
        meta_dict = {}
        if thread.pending_slots:
            try:
                meta_dict["presentedSlots"] = json.loads(thread.pending_slots)
            except Exception:
                pass
        if booking_confirmed:
            meta_dict["bookingConfirmed"] = True
            
        delivery_failure = None
        if dispatch_sms:
            # Genuine carrier webhooks are dispatched. The internal simulator
            # displays the stored reply and must never send a real SMS.
            dispatch_result = mobilemessage_service.send_sms(
                thread.customer_phone,
                assistant_reply,
                idempotency_key=system_message.id,
            )
            delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
        if dispatch_result.get("status") == "skipped" or (delivery_failure and ("skipped" in str(delivery_failure).lower() or "not configured" in str(delivery_failure).lower())):
            delivery_failure = None

        if delivery_failure:
            system_message.role = "draft"
            thread.state = "needs-review"
            event_log = ThreadEvent(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                type="draft-created",
                agent_id=None,
                meta=json.dumps({
                    "message_id": system_message.id,
                    "source": "sms-delivery-failed",
                    "reason": delivery_failure[:500],
                }),
                at=reply_at_naive,
            )
        else:
            event_log = ThreadEvent(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                type="auto-reply-sent",
                agent_id=None,
                meta=json.dumps(meta_dict),
                at=reply_at_naive,
            )
        db.add(system_message)
        db.add(event_log)
            
    db.commit()
    return booking_confirmed, slots_presented


def find_oldest_catch_up_candidate(db: Session):
    """Return the oldest conversation whose latest message is still unanswered."""
    candidates = []
    threads = db.query(Thread).filter(
        Thread.auto_reply_enabled.is_(True),
        Thread.state.in_(["auto-reply", "resolved"]),
    ).all()
    for thread in threads:
        latest = db.query(Message).filter(Message.thread_id == thread.id).order_by(
            Message.at.desc(), Message.id.desc()
        ).first()
        if not latest or latest.role != "customer":
            continue
        missed_events = db.query(ThreadEvent).filter(
            ThreadEvent.thread_id == thread.id,
            ThreadEvent.type == "ai-reply-missed",
        ).all()
        explicitly_missed = False
        for missed_event in missed_events:
            try:
                missed_meta = json.loads(missed_event.meta or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if missed_meta.get("message_id") == latest.id:
                explicitly_missed = True
                break
        # Old conversations predate explicit missed-message markers. Waiting
        # three minutes keeps this fallback clear of the normal 30–120s worker.
        old_enough = latest.at <= datetime.utcnow() - timedelta(minutes=3)
        if explicitly_missed or old_enough:
            candidates.append((latest.at, latest.id, thread, latest))
    if not candidates:
        return None
    _, _, thread, message = min(candidates, key=lambda item: (item[0], item[1]))
    return thread, message

def send_first_contact_auto_reply(
    db: Session,
    thread: Thread,
    customer_message: Message,
    config: Dict[str, Any],
    dispatch_sms: bool,
) -> None:
    reply_text = sanitize_outgoing_urls(config["message"])
    reply_at = datetime.utcnow()
    outbound = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        role="draft" if TRAINING_MODE_ENABLED else "system",
        text=reply_text,
        at=reply_at,
    )

    if TRAINING_MODE_ENABLED:
        thread.state = "needs-review"
        event_log = ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="draft-created",
            agent_id=None,
            meta=json.dumps({
                "message_id": outbound.id,
                "source": "first-contact-auto-responder",
                "customer_message_id": customer_message.id,
            }),
            at=reply_at,
        )
    else:
        delivery_failure = None
        if dispatch_sms:
            dispatch_result = mobilemessage_service.send_sms(
                thread.customer_phone,
                reply_text,
                idempotency_key=outbound.id,
            )
            delivery_failure = mobilemessage_service.delivery_error(dispatch_result)

        if delivery_failure:
            outbound.role = "draft"
            thread.state = "needs-review"
            event_log = ThreadEvent(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                type="draft-created",
                agent_id=None,
                meta=json.dumps({
                    "message_id": outbound.id,
                    "source": "first-contact-sms-delivery-failed",
                    "reason": delivery_failure[:500],
                    "customer_message_id": customer_message.id,
                }),
                at=reply_at,
            )
        else:
            event_log = ThreadEvent(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                type="auto-reply-sent",
                agent_id=None,
                meta=json.dumps({
                    "source": "first-contact-auto-responder",
                    "cooldownDays": config["cooldownDays"],
                    "customer_message_id": customer_message.id,
                }),
                at=reply_at,
            )

    db.add(outbound)
    db.add(event_log)
    db.commit()


def process_first_contact_auto_reply_delayed(
    thread_id: str,
    customer_message_id: str,
    config: Dict[str, Any],
    dispatch_sms: bool,
) -> None:
    import time

    delay_seconds = max(0, min(3600, int(config.get("delaySeconds", 0))))
    if delay_seconds:
        print(f"[First Contact Delay] Waiting {delay_seconds}s before replying on thread {thread_id}...")
        time.sleep(delay_seconds)

    db = SessionLocal()
    try:
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        customer_message = db.query(Message).filter(
            Message.id == customer_message_id,
            Message.thread_id == thread_id,
            Message.role == "customer",
        ).first()
        if not thread or not customer_message:
            print(f"[First Contact Delay] Thread or message no longer exists for {thread_id}. Reply canceled.")
            return
        if not thread.auto_reply_enabled or thread.state == "taken-over":
            print(f"[First Contact Delay] Automatic replies are off for {thread_id}. Reply canceled.")
            return

        current_config = load_first_contact_autoresponder()
        if not current_config["enabled"]:
            print(f"[First Contact Delay] First-contact responder is off. Reply canceled for {thread_id}.")
            return

        send_first_contact_auto_reply(db, thread, customer_message, config, dispatch_sms)
    except Exception as e:
        print(f"[First Contact Delay Error] {e}")
        db.rollback()
    finally:
        db.close()


def process_sms_reply_delayed(thread_id: str, body: str, provider_message_id: str, received_at_naive: datetime):
    import time
    import random
    
    delay = random.randint(30, 120)
    print(f"[Autoresponder Delay] Waiting {delay}s before replying on thread {thread_id}...")
    time.sleep(delay)
    
    db = SessionLocal()
    try:
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if not thread:
            print(f"[Autoresponder Delay] Thread {thread_id} not found. Skipping auto-reply.")
            return
            
        if not AUTO_REPLY_GLOBAL_ENABLED:
            print(f"[Autoresponder Delay] Global AI replies are off. Reply canceled for {thread_id}.")
            return
            
        if not thread.auto_reply_enabled:
            print(f"[Autoresponder Delay] Thread auto_reply_enabled is False. Reply canceled for {thread_id}.")
            return
            
        if thread.state == "taken-over":
            print(f"[Autoresponder Delay] Thread is taken-over. Reply canceled for {thread_id}.")
            return
            
        run_sms_reply_logic(db, thread_id, body, provider_message_id, received_at_naive)
    except Exception as e:
        print(f"[Autoresponder Delay Error] {e}")
        db.rollback()
    finally:
        db.close()


def should_process_sms_synchronously(
    is_testing: bool,
    is_simulation: bool = False,
) -> bool:
    """Tests, simulations, and the approval queue need an immediate response."""
    return is_testing or is_simulation or TRAINING_MODE_ENABLED


@app.post("/webhooks/sms")
def webhook_sms(payload: WebhookSMSInput, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    import sys
    from_phone = canonical_phone_number(payload.from_phone)
    received_at_naive = to_naive_utc(payload.receivedAt)
    provider_message_id = (payload.providerMessageId or "").strip() or None

    if provider_message_id:
        existing_message = db.query(Message).filter(
            Message.provider_message_id == provider_message_id,
            Message.role == "customer",
        ).first()
        if existing_message:
            print(f"[Webhook Deduplicated] Existing provider message {provider_message_id} ignored.")
            return {
                "status": "success",
                "thread_id": existing_message.thread_id,
                "duplicate": True,
            }

        receipt = InboundWebhookReceipt(
            provider_message_id=provider_message_id,
            from_phone=from_phone,
            received_at=received_at_naive,
        )
        db.add(receipt)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing_message = db.query(Message).filter(
                Message.provider_message_id == provider_message_id,
                Message.role == "customer",
            ).first()
            print(f"[Webhook Deduplicated] Concurrent provider message {provider_message_id} ignored.")
            return {
                "status": "success",
                "thread_id": existing_message.thread_id if existing_message else None,
                "duplicate": True,
            }
    
    first_contact_config = load_first_contact_autoresponder()
    first_contact_eligible = False

    # Locate or create thread by customer phone
    thread = find_thread_by_phone(db, from_phone)
    if (
        thread
        and first_contact_config["enabled"]
        and first_contact_config["message"]
        and thread.auto_reply_enabled
        and thread.state != "taken-over"
    ):
        cutoff = received_at_naive - timedelta(days=first_contact_config["cooldownDays"])
        recent_customer_message = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "customer",
            Message.at >= cutoff,
        ).first()
        first_contact_eligible = recent_customer_message is None
    
    if not thread:
        # Create a new thread
        thread = Thread(
            id=str(uuid.uuid4()),
            customer_phone=from_phone,
            state="auto-reply",
            priority="medium",
            sla_due_at=received_at_naive + timedelta(hours=24),
            unread_count=0,
            created_at=received_at_naive,
            updated_at=received_at_naive
        )
        db.add(thread)
        db.flush() # Populate thread.id
        first_contact_eligible = (
            first_contact_config["enabled"]
            and bool(first_contact_config["message"])
            and thread.auto_reply_enabled
            and thread.state != "taken-over"
        )
    
    # Append inbound customer message
    customer_message = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        role="customer",
        text=payload.body,
        provider_message_id=provider_message_id,
        at=received_at_naive
    )
    db.add(customer_message)

    if is_clear_customer_arrival(payload.body):
        record_customer_arrival_event(
            db,
            thread,
            customer_message.id,
            "clear-phrase",
        )
    
    # Increment unread_count
    thread.unread_count += 1
    thread.updated_at = datetime.utcnow()
    if not AUTO_REPLY_GLOBAL_ENABLED and thread.auto_reply_enabled and thread.state != "taken-over":
        db.add(ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="ai-reply-missed",
            agent_id=None,
            meta=json.dumps({"message_id": customer_message.id, "reason": "global-ai-off"}),
            at=received_at_naive,
        ))
    db.commit()
    
    is_testing = "pytest" in sys.modules or any("test" in arg for arg in sys.argv)
    if first_contact_eligible:
        background_tasks.add_task(
            process_first_contact_auto_reply_delayed,
            thread.id,
            customer_message.id,
            first_contact_config,
            not (is_testing or payload.isSimulation),
        )
        return {
            "status": "success",
            "thread_id": thread.id,
            "first_contact_auto_reply": True,
            "first_contact_delay_seconds": first_contact_config["delaySeconds"],
        }

    if should_process_sms_synchronously(is_testing, payload.isSimulation):
        # Training mode is an interactive approval workflow, so do not impose the
        # production typing delay before showing a draft.
        if AUTO_REPLY_GLOBAL_ENABLED and thread.auto_reply_enabled and thread.state != "taken-over":
            booking_confirmed, slots_presented = run_sms_reply_logic(
                db,
                thread.id,
                payload.body,
                payload.providerMessageId,
                received_at_naive,
                dispatch_sms=not (is_testing or payload.isSimulation),
            )
            res = {"status": "success", "thread_id": thread.id}
            if booking_confirmed:
                res["booking_confirmed"] = True
            if slots_presented:
                res["slots_presented"] = True
            return res
        else:
            return {"status": "success", "thread_id": thread.id}
    else:
        # Production: run in background task with a variable typing delay (30-120s)
        if AUTO_REPLY_GLOBAL_ENABLED and thread.auto_reply_enabled and thread.state != "taken-over":
            background_tasks.add_task(
                process_sms_reply_delayed,
                thread.id,
                payload.body,
                payload.providerMessageId,
                received_at_naive
            )
        return {"status": "success", "thread_id": thread.id}


@app.get("/api/threads")
def get_threads(
    search: Optional[str] = Query(None),
    filterStatus: Optional[str] = Query(None),
    filterPriority: Optional[str] = Query(None),
    onlyUnread: Optional[bool] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(Thread)
    
    if filterStatus:
        query = query.filter(Thread.state == filterStatus)
    if filterPriority:
        query = query.filter(Thread.priority == filterPriority)
    if onlyUnread:
        query = query.filter(Thread.unread_count > 0)
    
    if search:
        from sqlalchemy import or_
        query = query.outerjoin(Message).filter(
            or_(
                Thread.customer_phone.ilike(f"%{search}%"),
                Message.text.ilike(f"%{search}%")
            )
        ).distinct()
        
    threads = query.all()
    ordered_results = []
    
    for t in threads:
        last_msg = db.query(Message).filter(Message.thread_id == t.id).order_by(
            Message.at.desc(), Message.id.desc()
        ).first()
        last_activity_at = last_msg.at if last_msg else t.created_at
        last_message_at = format_dt(last_activity_at)
        last_arrival_event = db.query(ThreadEvent).filter(
            ThreadEvent.thread_id == t.id,
            ThreadEvent.type == "customer-arrived",
        ).order_by(ThreadEvent.at.desc(), ThreadEvent.id.desc()).first()
        
        assigned_agent_name = f"Agent {t.assigned_agent_id}" if t.assigned_agent_id else None
        
        result = {
            "id": t.id,
            "customerPhone": t.customer_phone,
            "lastMessageAt": last_message_at,
            "lastMessageText": last_msg.text if last_msg else "",
            "lastMessageRole": last_msg.role if last_msg else None,
            "lastArrivalAt": format_dt(last_arrival_event.at) if last_arrival_event else None,
            "lastArrivalEventId": last_arrival_event.id if last_arrival_event else None,
            "unreadCount": t.unread_count,
            "priority": t.priority,
            "status": t.state,
            "assignedAgentName": assigned_agent_name,
            "assignedAgentId": t.assigned_agent_id,
            "autoReplyEnabled": t.auto_reply_enabled,
            "sla": {
                "dueAt": format_dt(t.sla_due_at),
                "level": t.priority
            }
        }
        ordered_results.append((
            last_activity_at,
            last_msg.id if last_msg else "",
            t.id,
            result,
        ))

    ordered_results.sort(key=lambda item: item[:3], reverse=True)
    return [item[3] for item in ordered_results]


@app.post("/api/threads/catch-up")
def catch_up_missed_messages(db: Session = Depends(get_db)):
    """Draft one oldest unanswered conversation per call; never dispatch externally."""
    if not AUTO_REPLY_GLOBAL_ENABLED:
        raise HTTPException(status_code=409, detail="Turn AI on before catching up missed messages.")

    candidate = find_oldest_catch_up_candidate(db)
    if not candidate:
        return {"processed": False, "outcome": "complete"}

    thread, customer_message = candidate
    thread_id = thread.id
    try:
        run_sms_reply_logic(
            db,
            thread_id,
            customer_message.text,
            customer_message.provider_message_id or "catch-up",
            customer_message.at,
            dispatch_sms=False,
            draft_only=True,
        )
    except Exception as exc:
        db.rollback()
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if thread:
            thread.state = "needs-review"
            db.add(ThreadEvent(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                type="information-request",
                agent_id=None,
                meta=json.dumps({
                    "reason": f"Catch-up failed: {type(exc).__name__}",
                    "status": "pending",
                    "customer_message_id": customer_message.id,
                }),
                at=datetime.utcnow(),
            ))
            db.commit()
        return {"processed": True, "threadId": thread_id, "outcome": "information-request"}

    latest = db.query(Message).filter(Message.thread_id == thread_id).order_by(
        Message.at.desc(), Message.id.desc()
    ).first()
    outcome = "draft" if latest and latest.role == "draft" else "information-request"
    return {"processed": True, "threadId": thread_id, "outcome": outcome}


@app.get("/api/threads/{thread_id}")
def get_thread_detail(thread_id: str, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    now = datetime.utcnow()
    if now > thread.sla_due_at:
        sla_status = "breached"
    elif thread.sla_due_at - now < timedelta(hours=2):
        sla_status = "breaching"
    else:
        sla_status = "ok"
        
    assigned_agent = None
    if thread.assigned_agent_id:
        assigned_agent = {
            "id": thread.assigned_agent_id,
            "name": f"Agent {thread.assigned_agent_id}"
        }
        
    messages_list = []
    ordered_messages = db.query(Message).filter(Message.thread_id == thread.id).order_by(
        Message.at.asc(), Message.id.asc()
    ).all()
    for m in ordered_messages:
        messages_list.append({
            "id": m.id,
            "role": m.role,
            "text": m.text,
            "at": format_dt(m.at)
        })
        
    notes_list = []
    for n in sorted(thread.notes, key=lambda nt: nt.at):
        notes_list.append({
            "id": n.id,
            "agentId": n.agent_id,
            "text": n.text,
            "at": format_dt(n.at)
        })
        
    events_list = []
    for e in sorted(thread.events, key=lambda ev: ev.at):
        meta_parsed = {}
        if e.meta:
            try:
                meta_parsed = json.loads(e.meta)
            except Exception:
                meta_parsed = {"raw": e.meta}
                
        events_list.append({
            "id": e.id,
            "type": e.type,
            "agentId": e.agent_id,
            "at": format_dt(e.at),
            "meta": meta_parsed
        })
        
    return {
        "id": thread.id,
        "customerPhone": thread.customer_phone,
        "state": thread.state,
        "assignedAgent": assigned_agent,
        "autoReplyEnabled": thread.auto_reply_enabled,
        "sla": {
            "dueAt": format_dt(thread.sla_due_at),
            "level": thread.priority,
            "status": sla_status
        },
        "messages": messages_list,
        "notes": notes_list,
        "events": events_list
    }


@app.post("/api/threads/{thread_id}/takeover")
def takeover_thread(thread_id: str, payload: TakeoverInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.state = "taken-over"
    thread.assigned_agent_id = payload.agentId
    thread.unread_count = 0
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="takeover",
        agent_id=payload.agentId,
        meta=json.dumps({}),
        at=datetime.utcnow()
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "state": thread.state, "assignedAgentId": thread.assigned_agent_id}


OUTBOUND_SMS_SEND_LOCK = threading.Lock()
MANUAL_REPLY_DEDUPE_WINDOW = timedelta(minutes=5)


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


@app.post("/api/threads/{thread_id}/reply")
def reply_thread(thread_id: str, payload: ReplyInput, db: Session = Depends(get_db)):
    # Serialise manual gateway dispatches. This closes the race where a frozen
    # browser queues several POSTs before any one request commits its message.
    with OUTBOUND_SMS_SEND_LOCK:
        db.expire_all()
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        request_marker = f"manual-reply:{payload.clientRequestId}" if payload.clientRequestId else None
        if request_marker:
            existing_request = db.query(Message).filter(
                Message.thread_id == thread.id,
                Message.role == "agent",
                Message.provider_message_id == request_marker,
            ).first()
            if existing_request:
                print(f"[Manual SMS Deduplicated] Reused client request on thread {thread.id}.")
                return _manual_reply_response(existing_request, duplicate=True)

        now = datetime.utcnow()
        normalised_text = _normalise_manual_reply_text(payload.text)
        recent_agent_messages = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "agent",
            Message.at >= now - MANUAL_REPLY_DEDUPE_WINDOW,
        ).order_by(Message.at.desc(), Message.id.desc()).all()
        existing_same_text = next(
            (
                message
                for message in recent_agent_messages
                if _normalise_manual_reply_text(message.text) == normalised_text
            ),
            None,
        )
        if existing_same_text:
            print(f"[Manual SMS Deduplicated] Same reply already sent recently on thread {thread.id}.")
            return _manual_reply_response(existing_same_text, duplicate=True)

        # The content/time-bucket ID is stable even if a stalled UI creates a
        # fresh client request ID. It is also passed to the SMS gateway.
        five_minute_bucket = int(now.timestamp() // 300)
        message_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"assistant-ui:manual-reply:{thread.id}:{normalised_text}:{five_minute_bucket}",
        ))
        existing_message = db.query(Message).filter(Message.id == message_id).first()
        if existing_message:
            return _manual_reply_response(existing_message, duplicate=True)

        agent_message = Message(
            id=message_id,
            thread_id=thread.id,
            role="agent",
            text=payload.text,
            provider_message_id=request_marker,
            at=now,
        )
        dispatch_result = mobilemessage_service.send_sms(
            thread.customer_phone,
            payload.text,
            idempotency_key=agent_message.id,
        )
        delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
        if delivery_failure:
            raise HTTPException(status_code=502, detail=f"SMS was not sent. {delivery_failure[:500]}")

        db.add(agent_message)
        thread.updated_at = now
        thread.unread_count = 0
        db.commit()
        return _manual_reply_response(agent_message)


@app.post("/api/threads/{thread_id}/information-request/respond")
def respond_to_information_request(
    thread_id: str,
    payload: InformationRequestResponseInput,
    db: Session = Depends(get_db),
):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found.")
    if thread.state != "needs-review":
        raise HTTPException(status_code=409, detail="This conversation no longer needs information.")

    request_event = find_pending_information_request(db, thread_id, payload.requestEventId)
    if not request_event:
        raise HTTPException(status_code=409, detail="This information request has already been resolved.")
    try:
        request_meta = json.loads(request_event.meta or "{}")
    except (TypeError, json.JSONDecodeError):
        request_meta = {}

    customer_message = None
    customer_message_id = request_meta.get("customer_message_id")
    if customer_message_id:
        customer_message = db.query(Message).filter(
            Message.id == customer_message_id,
            Message.thread_id == thread.id,
            Message.role == "customer",
        ).first()
    if not customer_message:
        customer_message = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "customer",
        ).order_by(Message.at.desc(), Message.id.desc()).first()
    if not customer_message:
        raise HTTPException(status_code=409, detail="The customer message for this request no longer exists.")

    generated = generate_information_request_content(
        db,
        thread,
        customer_message,
        payload.information,
    )
    reply_text = generated["customer_reply"]
    outbound = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        role="system",
        text=reply_text,
        at=datetime.utcnow(),
    )

    # Persist the reusable fact first. If SMS delivery fails, retrying this
    # request safely replaces the same knowledge entry instead of duplicating it.
    knowledge_source = save_learned_information(
        request_event.id,
        customer_message.text,
        payload.information,
        generated["knowledge_summary"],
    )

    if not thread.customer_phone.startswith("locanto_"):
        dispatch_result = mobilemessage_service.send_sms(
            thread.customer_phone,
            reply_text,
            idempotency_key=outbound.id,
        )
        delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
        if delivery_failure:
            raise HTTPException(status_code=502, detail=f"SMS was not sent. {delivery_failure[:500]}")
    request_meta.update({
        "status": "resolved",
        "resolved_at": datetime.utcnow().isoformat() + "Z",
        "resolved_by": payload.agentId,
        "customer_message_id": customer_message.id,
        "knowledge_source": knowledge_source,
        "knowledge_summary": generated["knowledge_summary"],
        "reply_message_id": outbound.id,
    })
    request_event.meta = json.dumps(request_meta)
    db.add(outbound)
    db.add(ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="information-request-resolved",
        agent_id=payload.agentId,
        meta=json.dumps({
            "request_event_id": request_event.id,
            "message_id": outbound.id,
            "knowledge_source": knowledge_source,
        }),
        at=datetime.utcnow(),
    ))
    thread.state = "auto-reply"
    thread.unread_count = 0
    thread.updated_at = datetime.utcnow()
    db.commit()
    return {
        "status": "success",
        "message": {
            "id": outbound.id,
            "role": outbound.role,
            "text": outbound.text,
            "at": format_dt(outbound.at),
        },
        "knowledgeSource": knowledge_source,
        "knowledgeSummary": generated["knowledge_summary"],
    }


@app.post("/api/threads/{thread_id}/notes")
def add_thread_note(thread_id: str, payload: NoteInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    note = Note(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        agent_id=payload.agentId,
        text=payload.text,
        at=datetime.utcnow()
    )
    db.add(note)
    thread.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "id": note.id,
        "agentId": note.agent_id,
        "text": note.text,
        "at": format_dt(note.at)
    }


@app.post("/api/threads/{thread_id}/escalate")
def escalate_thread(thread_id: str, payload: EscalateInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.state = "escalated"
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="escalation",
        agent_id=payload.agentId,
        meta=json.dumps({"reason": payload.reason}),
        at=datetime.utcnow()
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "state": thread.state}


@app.post("/api/threads/{thread_id}/resolve")
def resolve_thread(thread_id: str, payload: ResolveInput, db: Session = Depends(get_db)):
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.state = "resolved"
    thread.updated_at = datetime.utcnow()
    
    event_log = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="resolution",
        agent_id=payload.agentId,
        meta=json.dumps({"summary": payload.summary}) if payload.summary else None,
        at=datetime.utcnow()
    )
    db.add(event_log)
    db.commit()
    
    return {"status": "success", "state": thread.state}


class BusinessVariableInput(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(default="", max_length=4000)
    description: Optional[str] = None
    required: Optional[bool] = False


class BusinessVariablesInput(BaseModel):
    variables: List[BusinessVariableInput] = Field(max_length=50)


MESSAGE_UI_SETTINGS_PATH = os.path.join(DATA_DIR, "message_ui_settings.json")


def load_message_ui_settings() -> dict[str, bool]:
    if not os.path.exists(MESSAGE_UI_SETTINGS_PATH):
        return {"showMessageAvatars": True}
    try:
        with open(MESSAGE_UI_SETTINGS_PATH, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict) and "showMessageAvatars" in saved:
            return {"showMessageAvatars": bool(saved["showMessageAvatars"])}
    except Exception:
        pass
    return {"showMessageAvatars": True}


@app.get("/api/admin/rag/status")
@app.get("/api/rag/status")
def get_rag_admin_status():
    """Return RAG index admin status, dataset hash, intent breakdown, and validation status."""
    if not STYLE_EXAMPLES_ENABLED or example_index is None:
        return {
            "enabled": False,
            "feature_flag_enabled": False,
            "rag_state": "disabled",
            "dataset_path": str(DATASET_FILE),
            "validation_status": "disabled",
            "validation_error": None,
            "total_examples": 0,
            "intent_counts": {},
            "dataset_hash": None,
            "last_validated_at": None,
        }
    return example_index.get_status_metadata()


@app.get("/api/settings/business-variables")
def get_business_variables():
    return {"variables": load_business_variables()}


@app.post("/api/settings/business-variables")
def save_business_variables(payload: BusinessVariablesInput):
    normalized = []
    seen = set()
    for item in payload.variables:
        key = item.key.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise HTTPException(
                status_code=422,
                detail=f'Variable "{item.key}" must start with a letter and use only lowercase letters, numbers, and underscores.',
            )
        if key in RESERVED_TEMPLATE_VARIABLES:
            raise HTTPException(status_code=422, detail=f'Variable "{key}" is reserved by the application.')
        if key in seen:
            raise HTTPException(status_code=422, detail=f'Variable "{key}" is duplicated.')
        seen.add(key)
        entry = {
            "key": key,
            "label": item.label.strip(),
            "value": item.value.strip(),
        }
        if item.description is not None:
            entry["description"] = item.description.strip()
        if item.required is not None:
            entry["required"] = bool(item.required)
        normalized.append(entry)
    try:
        os.makedirs(os.path.dirname(BUSINESS_VARIABLES_PATH), exist_ok=True)
        with open(BUSINESS_VARIABLES_PATH, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, ensure_ascii=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save business variables: {exc}")
    return {"status": "success", "variables": load_business_variables()}


@app.get("/api/settings")
def get_settings():
    api_key = os.getenv("OPENAI_API_KEY") or ""
    if api_key:
        if len(api_key) > 12:
            obfuscated_api_key = f"{api_key[:8]}...{api_key[-4:]}"
        else:
            obfuscated_api_key = f"{api_key[:3]}"
    else:
        obfuscated_api_key = ""
        
    system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
    system_prompt_content = "You are a helpful, friendly customer service agent. Use the context and slots."
    if os.path.exists(system_prompt_path):
        try:
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                system_prompt_content = f.read()
        except Exception:
            pass
            
    user_prompt_path = os.path.join(PROMPTS_DIR, "user_prompt.txt")
    user_prompt_content = "Customer message: {message}\nKnowledge context:\n{knowledge}\nCalendar openings:\n{slots}"
    if os.path.exists(user_prompt_path):
        try:
            with open(user_prompt_path, "r", encoding="utf-8") as f:
                user_prompt_content = f.read()
        except Exception:
            pass
            
    has_google_credentials = (
        bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")) or
        os.path.exists(os.path.join(BASE_DIR, "service_account.json")) or
        os.path.exists(os.path.join(BASE_DIR, "credentials.json"))
    )
    
    return {
        "openaiApiKey": obfuscated_api_key,
        "systemPrompt": system_prompt_content,
        "userPrompt": user_prompt_content,
        "hasGoogleCredentials": has_google_credentials,
        "autoReplyGlobalEnabled": AUTO_REPLY_GLOBAL_ENABLED,
        "trainingModeEnabled": TRAINING_MODE_ENABLED,
        "showMessageAvatars": load_message_ui_settings()["showMessageAvatars"],
    }


@app.post("/api/settings")
def update_settings(payload: SettingsUpdateInput):
    if payload.systemPrompt is not None:
        system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
        os.makedirs(os.path.dirname(system_prompt_path), exist_ok=True)
        with open(system_prompt_path, "w", encoding="utf-8") as f:
            f.write(payload.systemPrompt)

    if payload.userPrompt is not None:
        user_prompt_path = os.path.join(PROMPTS_DIR, "user_prompt.txt")
        os.makedirs(os.path.dirname(user_prompt_path), exist_ok=True)
        with open(user_prompt_path, "w", encoding="utf-8") as f:
            f.write(payload.userPrompt)

    if payload.autoReplyGlobalEnabled is not None:
        global AUTO_REPLY_GLOBAL_ENABLED
        AUTO_REPLY_GLOBAL_ENABLED = payload.autoReplyGlobalEnabled
        auto_reply_path = os.path.join(DATA_DIR, "auto_reply_global.json")
        try:
            os.makedirs(os.path.dirname(auto_reply_path), exist_ok=True)
            with open(auto_reply_path, "w", encoding="utf-8") as f:
                json.dump({"enabled": payload.autoReplyGlobalEnabled}, f, indent=2)
        except Exception as e:
            print(f"Failed to save global auto reply state: {e}")

    if payload.trainingModeEnabled is not None:
        global TRAINING_MODE_ENABLED
        TRAINING_MODE_ENABLED = payload.trainingModeEnabled
        training_mode_path = os.path.join(DATA_DIR, "training_mode.json")
        try:
            os.makedirs(os.path.dirname(training_mode_path), exist_ok=True)
            with open(training_mode_path, "w", encoding="utf-8") as f:
                json.dump({"enabled": payload.trainingModeEnabled}, f, indent=2)
        except Exception as e:
            print(f"Failed to save training mode state: {e}")

    if payload.showMessageAvatars is not None:
        try:
            os.makedirs(os.path.dirname(MESSAGE_UI_SETTINGS_PATH), exist_ok=True)
            with open(MESSAGE_UI_SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump({"showMessageAvatars": payload.showMessageAvatars}, handle, indent=2)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to save message UI settings: {exc}")

    if payload.openaiApiKey and "..." not in payload.openaiApiKey:
        env_path = os.path.join(BASE_DIR, ".env")
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        key_found = False
        for i, line in enumerate(lines):
            if line.strip().startswith("OPENAI_API_KEY="):
                lines[i] = f"OPENAI_API_KEY={payload.openaiApiKey}\n"
                key_found = True
                break
        if not key_found:
            lines.append(f"OPENAI_API_KEY={payload.openaiApiKey}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        if DOTENV_AVAILABLE:
            load_dotenv(override=True)

        global openai_client
        if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            try:
                openai_client = OpenAI()
                print("OpenAI client re-initialized.")
            except Exception as e:
                print(f"OpenAI client re-initialization failed: {e}")

    return {"status": "success"}


class QARuleItem(BaseModel):
    id: str
    trigger: str
    reply: str


@app.get("/api/qa-rules")
def get_qa_rules():
    qa_path = os.path.join(BASE_DIR, "data", "qa_rules.json")
    if os.path.exists(qa_path):
        try:
            with open(qa_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


@app.post("/api/qa-rules")
def save_qa_rules(rules: list[QARuleItem]):
    qa_path = os.path.join(BASE_DIR, "data", "qa_rules.json")
    try:
        os.makedirs(os.path.dirname(qa_path), exist_ok=True)
        with open(qa_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump() for r in rules], f, indent=2)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save QA rules: {e}")

@app.get("/api/settings/first-contact-autoresponder")
def get_first_contact_autoresponder():
    return load_first_contact_autoresponder()

@app.post("/api/settings/first-contact-autoresponder")
def save_first_contact_autoresponder(payload: FirstContactAutoresponderInput):
    config = payload.model_dump()
    try:
        os.makedirs(os.path.dirname(FIRST_CONTACT_AUTORESPONDER_PATH), exist_ok=True)
        temp_path = f"{FIRST_CONTACT_AUTORESPONDER_PATH}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(temp_path, FIRST_CONTACT_AUTORESPONDER_PATH)
        return {"status": "success", **config}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save first-contact auto-responder settings: {e}",
        )


@app.post("/api/messages/{message_id}/approve")
def approve_draft_message(message_id: str, db: Session = Depends(get_db)):
    with OUTBOUND_SMS_SEND_LOCK:
        db.expire_all()
        msg = db.query(Message).filter(Message.id == message_id).first()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found.")
        if msg.role == "agent":
            print(f"[Draft Approval Deduplicated] Draft {message_id} was already sent.")
            return {"status": "success", "duplicate": True}
        if msg.role != "draft":
            raise HTTPException(status_code=400, detail="Only draft messages can be approved.")

        thread = db.query(Thread).filter(Thread.id == msg.thread_id).first()
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found.")

        if not thread.customer_phone.startswith("locanto_"):
            dispatch_result = mobilemessage_service.send_sms(
                thread.customer_phone,
                msg.text,
                idempotency_key=msg.id,
            )
            delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
            if delivery_failure:
                raise HTTPException(status_code=502, detail=f"SMS was not sent. {delivery_failure[:500]}")

        msg.role = "agent"
        msg.at = datetime.utcnow()

        draft_event = db.query(ThreadEvent).filter(
            ThreadEvent.thread_id == thread.id,
            ThreadEvent.type == "draft-created",
        ).order_by(ThreadEvent.at.desc()).all()
        is_catch_up_draft = False
        for event_item in draft_event:
            try:
                event_meta = json.loads(event_item.meta or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if event_meta.get("message_id") == message_id:
                is_catch_up_draft = event_meta.get("source") == "catch-up"
                break

        other_drafts = db.query(Message).filter(
            Message.thread_id == thread.id,
            Message.role == "draft",
            Message.id != message_id,
        ).count()
        if other_drafts == 0:
            thread.state = "auto-reply" if is_catch_up_draft else "taken-over"
        thread.unread_count = 0

        approval_event = ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="auto-reply-sent",
            agent_id="manual-approval",
            meta=json.dumps({"message_id": message_id}),
            at=datetime.utcnow(),
        )
        db.add(approval_event)
        db.commit()
        return {"status": "success", "duplicate": False}


@app.post("/api/messages/{message_id}/discard")
def discard_draft_message(message_id: str, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found.")
    if msg.role != "draft":
        raise HTTPException(status_code=400, detail="Only draft messages can be discarded.")
        
    thread = db.query(Thread).filter(Thread.id == msg.thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found.")
        
    # Delete message
    db.delete(msg)
    
    # Resolve needs-review state of thread if no other drafts exist
    other_drafts = db.query(Message).filter(Message.thread_id == thread.id, Message.role == "draft", Message.id != message_id).count()
    if other_drafts == 0:
        thread.state = "taken-over"
        
    # Log discard event
    discard_event = ThreadEvent(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        type="draft-discarded",
        agent_id="manual-discard",
        meta=json.dumps({"message_id": message_id}),
        at=datetime.utcnow()
    )
    db.add(discard_event)
    db.commit()
    return {"status": "success"}


@app.get("/api/settings/knowledge-files")
def get_knowledge_files():
    knowledge_dir = KNOWLEDGE_DIR
    files_list = []
    if os.path.exists(knowledge_dir):
        for filename in os.listdir(knowledge_dir):
            filepath = os.path.join(knowledge_dir, filename)
            if os.path.isfile(filepath):
                files_list.append({
                    "name": filename,
                    "sizeBytes": os.path.getsize(filepath)
                })
    return files_list


@app.post("/api/settings/upload-knowledge")
def upload_knowledge_file(file: UploadFile = File(...)):
    knowledge_dir = KNOWLEDGE_DIR
    os.makedirs(knowledge_dir, exist_ok=True)
    filepath = os.path.join(knowledge_dir, file.filename)
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Reload and re-index
    load_knowledge_base()
    
    return {"status": "success", "filename": file.filename}


@app.post("/api/settings/upload-credentials")
def upload_credentials_file(file: UploadFile = File(...)):
    dest_path = os.path.join(BASE_DIR, "service_account.json")
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    global calendar_service
    calendar_service = GoogleCalendarService(SessionLocal)
    
    return {"status": "success"}


class FileSaveInput(BaseModel):
    content: str

class FileSearchInput(BaseModel):
    query: str

class FilePurgeInput(BaseModel):
    query: Optional[str] = None
    indices: Optional[List[int]] = None


@app.get("/api/settings/knowledge-files/{filename}")
def get_knowledge_file_content(filename: str):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")


@app.post("/api/settings/knowledge-files/{filename}")
def save_knowledge_file_content(filename: str, payload: FileSaveInput):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(payload.content)
            
        load_knowledge_base()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")


@app.delete("/api/settings/knowledge-files/{filename}")
def delete_knowledge_file(filename: str):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        os.remove(filepath)
        load_knowledge_base()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")


@app.post("/api/settings/knowledge-files/{filename}/search")
def search_knowledge_file_lines(filename: str, payload: FileSearchInput):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    q = payload.query.lower().strip()
    results = []
    total = 0
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    text_to_search = json.dumps(obj).lower()
                    if q in text_to_search:
                        total += 1
                        if len(results) < 100:
                            results.append({
                                "index": idx,
                                "input": obj.get("input", ""),
                                "output": obj.get("output", "")
                            })
                except Exception:
                    if q in line.lower():
                        total += 1
                        if len(results) < 100:
                            results.append({
                                "index": idx,
                                "input": line,
                                "output": ""
                            })
        return {"results": results, "totalMatches": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search file: {e}")


@app.post("/api/settings/knowledge-files/{filename}/purge")
def purge_knowledge_file_lines(filename: str, payload: FilePurgeInput):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        purged_lines = []
        purged_count = 0
        
        q = payload.query.lower().strip() if payload.query else None
        indices_set = set(payload.indices) if payload.indices is not None else set()
        
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
                
            if idx in indices_set:
                purged_count += 1
                continue
                
            if q:
                matched = False
                try:
                    obj = json.loads(line)
                    inp = str(obj.get("input", "")).lower()
                    out = str(obj.get("output", "")).lower()
                    if q in inp or q in out:
                        matched = True
                except Exception:
                    if q in line.lower():
                        matched = True
                        
                if matched:
                    purged_count += 1
                    continue
                    
            purged_lines.append(line)
            
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(purged_lines)
            
        load_knowledge_base()
        return {"status": "success", "purgedCount": purged_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to purge file: {e}")


# --- Services, SMS Confirmation & Booking Form Endpoints ---

class ServiceItem(BaseModel):
    id: str
    name: str
    description: str
    price: int
    duration: int
    showDuration: Optional[bool] = True

class ServicesListInput(BaseModel):
    services: List[ServiceItem]

class SmsConfirmationInput(BaseModel):
    template: str

class ManualBookingInput(BaseModel):
    serviceId: str
    name: str
    phone: str
    startTime: str
    notes: Optional[str] = None


@app.get("/api/services")
def get_services():
    services_path = os.path.join(DATA_DIR, "services.json")
    if os.path.exists(services_path):
        try:
            with open(services_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


@app.post("/api/services")
def save_services(payload: ServicesListInput):
    services_path = os.path.join(DATA_DIR, "services.json")
    try:
        os.makedirs(os.path.dirname(services_path), exist_ok=True)
        services_dict = [item.model_dump() for item in payload.services]
        with open(services_path, "w", encoding="utf-8") as f:
            json.dump(services_dict, f, indent=2)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save services: {e}")


@app.get("/api/settings/sms-confirmation")
def get_sms_confirmation():
    template_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt")
    template = "Hi {name}, your booking for {service} on {time} is confirmed! See you then. - Tori"
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
        except Exception:
            pass
    return {"template": template}


@app.post("/api/settings/sms-confirmation")
def save_sms_confirmation(payload: SmsConfirmationInput):
    template_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt")
    try:
        os.makedirs(os.path.dirname(template_path), exist_ok=True)
        with open(template_path, "w", encoding="utf-8") as f:
            f.write(payload.template)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save SMS confirmation template: {e}")

@app.post("/api/calendar/bookings")
def create_manual_booking(payload: ManualBookingInput, db: Session = Depends(get_db)):
    normalized_destination = mobilemessage_service.normalize_sms_destination(payload.phone)
    if not normalized_destination:
        raise HTTPException(
            status_code=422,
            detail=(
                "Enter a valid Australian mobile number in 04xx xxx xxx "
                "or +614xx xxx xxx format."
            ),
        )

    try:
        customer_phone = "+" + normalized_destination
        start_dt = datetime.fromisoformat(payload.startTime.replace("Z", ""))
        
        services_path = os.path.join(DATA_DIR, "services.json")
        services = []
        if os.path.exists(services_path):
            with open(services_path, "r", encoding="utf-8") as f:
                services = json.load(f)
                
        service = None
        for s in services:
            if s.get("id") == payload.serviceId:
                service = s
                break
                
        if not service:
            service = {
                "name": "Custom Appointment",
                "duration": 60,
                "price": 100
            }
            
        duration = service.get("duration", 60)
        end_dt = start_dt + timedelta(minutes=duration)
        
        summary = f"{payload.name} - {service['name']}"
        success = calendar_service.create_booking(
            summary=summary,
            start=start_dt,
            end=end_dt,
            customer_phone=customer_phone
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to create booking in calendar service.")
            
        template_path = os.path.join(PROMPTS_DIR, "sms_confirmation_template.txt")
        template = "Hi {name}, your booking for {service} on {time} is confirmed! See you then. - Tori"
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
                
        formatted_time = start_dt.strftime("%A, %b %d at %I:%M %p")
        confirmation_variables = {
            **get_business_variable_values(),
            "name": payload.name,
            "service": service["name"],
            "time": formatted_time,
        }
        sms_text = render_template_variables(template, confirmation_variables)
        
        # Load website-only display confirmation screen template
        screen_template_path = os.path.join(PROMPTS_DIR, "website_confirmation_template.txt")
        screen_template = (
            "Hi {name}, your booking for {service} on {time} is confirmed!\n\n"
            "You will receive an SMS from me shortly with the address details.\n\n"
            "If you do not receive it in the next 20 minutes, please send me a message. See you then! - Tori"
        )
        if os.path.exists(screen_template_path):
            try:
                with open(screen_template_path, "r", encoding="utf-8") as f:
                    screen_template = f.read()
            except Exception:
                pass
        screen_text = render_template_variables(screen_template, confirmation_variables)

        thread = find_thread_by_phone(db, customer_phone)
        if not thread:
            thread = Thread(
                id=str(uuid.uuid4()),
                customer_phone=customer_phone,
                state="resolved",
                priority="medium",
                sla_due_at=start_dt + timedelta(hours=24),
                unread_count=0,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(thread)
            db.flush()
        else:
            thread.state = "resolved"
            
        confirmation_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="agent",
            text=sms_text,
            at=datetime.utcnow()
        )
        dispatch_result = mobilemessage_service.send_sms(
            customer_phone,
            sms_text,
            idempotency_key=confirmation_msg.id,
        )
        delivery_failure = mobilemessage_service.delivery_error(dispatch_result)
        if dispatch_result.get("status") == "skipped" or (delivery_failure and ("skipped" in str(delivery_failure).lower() or "not configured" in str(delivery_failure).lower())):
            delivery_failure = None
        if delivery_failure:
            confirmation_msg.role = "draft"
            thread.state = "needs-review"
        db.add(confirmation_msg)
        
        event = ThreadEvent(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            type="sms-delivery-failed" if delivery_failure else "resolution",
            agent_id="system",
            meta=json.dumps({
                "detail": f"Booked {service['name']} for {payload.name}",
                **({"reason": delivery_failure[:500]} if delivery_failure else {}),
            }),
            at=datetime.utcnow()
        )
        db.add(event)
        db.commit()
        
        return {
            "status": "partial" if delivery_failure else "success",
            "smsSent": "" if delivery_failure else screen_text,
            "smsError": "Booking saved, but the confirmation SMS was not sent." if delivery_failure else None,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Booking creation failed: {e}")


# --- Working Hours Endpoints ---

class WorkingHourEntry(BaseModel):
    day: str
    enabled: bool
    open: str
    close: str

class WorkingHoursInput(BaseModel):
    hours: List[WorkingHourEntry]


@app.get("/api/settings/working-hours")
def get_working_hours():
    return load_working_hours()


@app.post("/api/settings/working-hours")
def save_working_hours(payload: WorkingHoursInput):
    try:
        os.makedirs(os.path.dirname(WORKING_HOURS_PATH), exist_ok=True)
        data = [entry.model_dump() for entry in payload.hours]
        with open(WORKING_HOURS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save working hours: {e}")


# --- MobileMessage Settings Endpoints ---


class MobileMessageConfigInput(BaseModel):
    username: str
    password: str
    sender: Optional[str] = ""
    enabled: bool = False

@app.get("/api/settings/mobilemessage")
def get_mobilemessage_settings():
    config = mobilemessage_service.load_config()
    return {
        "username": config.get("username", ""),
        "password": "",
        "hasPassword": bool(config.get("password")),
        "sender": config.get("sender", ""),
        "enabled": bool(config.get("enabled", False)),
    }

@app.post("/api/settings/mobilemessage")
def save_mobilemessage_settings(payload: MobileMessageConfigInput):
    config = payload.model_dump()
    if not config.get("password"):
        config["password"] = mobilemessage_service.load_config().get("password", "")
    config["enabled"] = bool(config.get("username") and config.get("password") and payload.enabled)
    success = mobilemessage_service.save_config(config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save MobileMessage configuration.")
    return {"status": "success"}


# --- Locanto Auto-Responder Sync Endpoint ---

class LocantoMessagePayload(BaseModel):
    event: str
    sender: str
    adTitle: Optional[str] = "Locanto Ad"
    messageSnippet: str
    timestamp: str

@app.post("/api/locanto/sync")
def handle_locanto_message(payload: LocantoMessagePayload, db: Session = Depends(get_db)):
    """
    Receives incoming Locanto buyer messages from Playwright engine,
    stores them in assistant.db, generates a gpt-5.6-terra AI reply in character,
    stores the reply, and returns {"replyText": "..."}.
    """
    try:
        customer_id = f"locanto_{payload.sender.strip().lower()}"
        
        # 1. Get or create thread for this Locanto buyer
        thread = db.query(Thread).filter(Thread.customer_phone == customer_id).first()
        if not thread:
            thread = Thread(
                id=str(uuid.uuid4()),
                customer_phone=customer_id,
                state="auto-reply",
                priority="medium",
                sla_due_at=datetime.utcnow() + timedelta(hours=2),
                auto_reply_enabled=True
            )
            db.add(thread)
            db.commit()
            db.refresh(thread)

        # 2. Record incoming customer message
        incoming_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role="customer",
            text=f"[{payload.adTitle}] {payload.messageSnippet}",
            at=datetime.utcnow()
        )
        db.add(incoming_msg)
        db.commit()

        reply_text = None
        
        # Check Q&A Rules first
        qa_reply = match_qa_rule(payload.messageSnippet)
        if qa_reply:
            print(f"[QA Rules Match] Locanto trigger matched. Using pre-configured reply.")
            reply_text = qa_reply
        # Check if auto-reply is enabled
        elif AUTO_REPLY_GLOBAL_ENABLED and openai_client:
            # A. Read uploaded knowledge plus the live Settings catalogue.
            retrieved_context = build_business_context(payload.messageSnippet)

            # B. Query next 3 available slots
            from zoneinfo import ZoneInfo
            tz_hobart = ZoneInfo("Australia/Hobart")
            dt = datetime.now(tz_hobart)
            minutes = 15 * ((dt.minute + 14) // 15)
            dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)

            wh = load_working_hours()
            wh_by_day = {entry["day"]: entry for entry in wh}

            busy_slots = calendar_service.get_busy_slots(dt, dt + timedelta(days=14))
            free_slots = []
            limit_dt = dt + timedelta(days=14)
            while dt < limit_dt and len(free_slots) < 3:
                day_name = DAY_NAMES[dt.weekday()]
                day_cfg = wh_by_day.get(day_name)
                if day_cfg and day_cfg.get("enabled", False):
                    open_h, open_m = map(int, day_cfg["open"].split(":"))
                    close_h, close_m = map(int, day_cfg["close"].split(":"))
                    open_mins = open_h * 60 + open_m
                    close_mins = close_h * 60 + close_m
                    dt_mins = dt.hour * 60 + dt.minute
                    slot_end = dt + timedelta(minutes=30)
                    slot_end_mins = slot_end.hour * 60 + slot_end.minute
                    if dt_mins >= open_mins and slot_end_mins <= close_mins:
                        overlap = False
                        for b in busy_slots:
                            if dt < b["end"] and slot_end > b["start"]:
                                overlap = True
                                break
                        if not overlap:
                            free_slots.append((dt, slot_end))
                dt += timedelta(minutes=15)

            slots_str = ""
            for i, (s, e) in enumerate(free_slots):
                slots_str += f"- Option {i+1}: {s.isoformat()} to {e.isoformat()}\n"
            if not slots_str:
                slots_str = "No openings available."
            broad_availability_guidance = build_broad_availability_guidance(
                payload.messageSnippet,
                datetime.now(tz_hobart),
                busy_slots,
                wh_by_day,
            )
            if broad_availability_guidance:
                slots_str += f"\n{broad_availability_guidance}"

            # C. Load prompt templates
            system_prompt_path = os.path.join(PROMPTS_DIR, "system_prompt.txt")
            user_prompt_path = os.path.join(PROMPTS_DIR, "user_prompt.txt")
            
            system_prompt_tmpl = "You are a helpful, friendly customer service agent. Use the context and slots."
            if os.path.exists(system_prompt_path):
                with open(system_prompt_path, "r", encoding="utf-8") as f:
                    system_prompt_tmpl = f.read()
                    
            user_prompt_tmpl = "Customer message: {message}\nKnowledge context:\n{knowledge}\nCalendar openings:\n{slots}"
            if os.path.exists(user_prompt_path):
                with open(user_prompt_path, "r", encoding="utf-8") as f:
                    user_prompt_tmpl = f.read()
                    
            business_variables = get_business_variable_values()
            system_prompt_rendered = render_template_variables(system_prompt_tmpl, {
                **business_variables,
                "current_time": datetime.now(tz_hobart).strftime("%A %d %B %Y, %I:%M %p %Z"),
            })
            current_history_text = f"[{payload.adTitle}] {payload.messageSnippet}"
            user_prompt_rendered = render_template_variables(user_prompt_tmpl, {
                **business_variables,
                "message": current_history_text,
                "knowledge": retrieved_context,
                "slots": slots_str,
            })

            examples = get_style_examples(payload.messageSnippet)
            instructions = build_model_instructions(
                system_prompt_rendered,
                examples,
                STYLE_PROFILE_STORE.get_applied(),
            )

            # E. Input conversation history
            history_msgs = db.query(Message).filter(Message.thread_id == thread.id).order_by(Message.at.asc()).all()
            input_history = build_model_input(
                history_msgs,
                current_history_text=current_history_text,
                enriched_current_prompt=user_prompt_rendered,
            )

            # F. Call gpt-5.6-terra Responses API
            try:
                response = openai_client.responses.create(
                    model="gpt-5.6-terra",
                    instructions=instructions,
                    input=input_history,
                    store=False
                )
                reply_text = response.output_text
            except Exception as openai_err:
                print(f"[Locanto API Error] OpenAI failed: {openai_err}")
                reply_text = "Hey, I've got your message but I can't check that properly right now. I'll get back to you shortly."

        reply_text = sanitize_outgoing_urls(reply_text)
        if reply_text:
            if TRAINING_MODE_ENABLED:
                outbound_msg = Message(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    role="draft",
                    text=reply_text,
                    at=datetime.utcnow()
                )
                db.add(outbound_msg)
                thread.state = "needs-review"
                
                # Log draft-created event
                event_log = ThreadEvent(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    type="draft-created",
                    agent_id=None,
                    meta=json.dumps({"message_id": outbound_msg.id, "locanto_ad": payload.adTitle}),
                    at=datetime.utcnow()
                )
                db.add(event_log)
                db.commit()
                
                return {
                    "status": "success",
                    "replyText": None
                }
            else:
                outbound_msg = Message(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    role="agent",
                    text=reply_text,
                    at=datetime.utcnow()
                )
                db.add(outbound_msg)

                # Log auto-reply-sent event
                event_log = ThreadEvent(
                    id=str(uuid.uuid4()),
                    thread_id=thread.id,
                    type="auto-reply-sent",
                    agent_id=None,
                    meta=json.dumps({"locanto_ad": payload.adTitle}),
                    at=datetime.utcnow()
                )
                db.add(event_log)
                db.commit()
                
                return {
                    "status": "success",
                    "replyText": reply_text
                }
        return {"status": "success", "replyText": None}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process Locanto message: {e}")

# --- Isolated multi-persona Boot Camp ---

BOOTCAMP_HANDOFF_RE = re.compile(
    r"\[\[HANDOFF(?::\s*(.*?))?\]\]",
    re.IGNORECASE | re.DOTALL,
)
BOOTCAMP_REFUSAL_RE = re.compile(
    r"\b(?:i can(?:not|'t)|i(?:'m| am) unable|can't assist|cannot assist|"
    r"not able to help|must have been a mistake|don't offer that|do not offer that)\b",
    re.IGNORECASE,
)


def build_read_only_calendar_context(now: Optional[datetime] = None) -> str:
    """Build a compact availability snapshot without exposing calendar write actions."""
    from zoneinfo import ZoneInfo

    tz_hobart = ZoneInfo("Australia/Hobart")
    if now is None:
        local_now = datetime.now(tz_hobart)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=tz_hobart)
    else:
        local_now = now.astimezone(tz_hobart)

    limit = local_now + timedelta(days=14)
    busy_slots = calendar_service.get_busy_slots(local_now, limit)
    working_lines = []
    for entry in load_working_hours():
        if entry.get("enabled", False):
            working_lines.append(f"{entry['day']} {entry['open']}-{entry['close']}")
        else:
            working_lines.append(f"{entry['day']} closed")

    busy_lines = []
    for busy in busy_slots:
        start = busy["start"].astimezone(tz_hobart)
        end = busy["end"].astimezone(tz_hobart)
        busy_lines.append(
            f"- {start.strftime('%A %d %B %Y, %I:%M %p')} to {end.strftime('%I:%M %p')}"
        )
    if not busy_lines:
        busy_lines.append("- None")

    return (
        "READ-ONLY CALENDAR SNAPSHOT\n"
        f"Current local time (Australia/Hobart): {local_now.strftime('%A %d %B %Y, %I:%M %p %Z')}\n"
        f"Window ends: {limit.strftime('%A %d %B %Y, %I:%M %p %Z')}\n"
        f"Working hours: {'; '.join(working_lines)}\n"
        "Busy periods (never offer an overlapping time):\n"
        + "\n".join(busy_lines)
        + "\nAll other times inside working hours are available for enquiry. "
        "This snapshot may be used to answer availability, but Boot Camp cannot create, "
        "change, cancel, or confirm a booking."
    )


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


BOOTCAMP_RUNNER = BootcampRunner(
    store=BOOTCAMP_STORE,
    openings=load_opening_messages(BOOTCAMP_OPENINGS_FILE),
    generate_tori=generate_bootcamp_tori_reply,
    generate_persona=generate_bootcamp_persona_reply,
    max_workers=int(os.getenv("BOOTCAMP_MAX_WORKERS", "6")),
    message_delay_seconds=float(os.getenv("BOOTCAMP_MESSAGE_DELAY_SECONDS", "2.5")),
)


class BootcampRunInput(BaseModel):
    personaIds: List[str]
    maxTurns: int = 5
    styleProfile: Dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_STYLE_PROFILE))


class BootcampControlInput(BaseModel):
    operation: str


class BootcampProfileInput(BaseModel):
    styleProfile: Dict[str, int]


class BootcampInformationRequestInput(BaseModel):
    information: str = Field(min_length=1, max_length=6000)

    @model_validator(mode="after")
    def clean_information(self):
        self.information = self.information.strip()
        if not self.information:
            raise ValueError("Information is required.")
        return self


@app.get("/api/bootcamp/personas")
def get_bootcamp_personas():
    return BOOTCAMP_PERSONAS


@app.get("/api/bootcamp/profile")
def get_bootcamp_profile():
    return {
        "active": STYLE_PROFILE_STORE.get_active(),
        "defaults": DEFAULT_STYLE_PROFILE,
        "isApplied": STYLE_PROFILE_STORE.is_applied(),
        "canUndo": STYLE_PROFILE_STORE.can_undo(),
    }


@app.post("/api/bootcamp/profile/apply")
def apply_bootcamp_profile(payload: BootcampProfileInput):
    return {
        "active": STYLE_PROFILE_STORE.apply(payload.styleProfile),
        "isApplied": True,
        "canUndo": True,
    }


@app.post("/api/bootcamp/profile/undo")
def undo_bootcamp_profile():
    active = STYLE_PROFILE_STORE.undo()
    return {
        "active": active,
        "isApplied": STYLE_PROFILE_STORE.is_applied(),
        "canUndo": STYLE_PROFILE_STORE.can_undo(),
    }


@app.post("/api/bootcamp/runs")
def start_bootcamp_run(payload: BootcampRunInput):
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI is not configured")
    if not payload.personaIds:
        raise HTTPException(status_code=400, detail="Select at least one persona")
    run_id = BOOTCAMP_RUNNER.start(
        payload.personaIds,
        max(1, min(20, payload.maxTurns)),
        normalize_style_profile(payload.styleProfile),
    )
    return BOOTCAMP_STORE.get_run(run_id)


@app.get("/api/bootcamp/runs/latest")
def get_latest_bootcamp_run():
    return {"run": BOOTCAMP_STORE.latest_run()}


@app.get("/api/bootcamp/runs/{run_id}")
def get_bootcamp_run(run_id: str):
    run = BOOTCAMP_STORE.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Boot Camp run not found")
    return run


@app.post("/api/bootcamp/conversations/{conversation_id}/information-request/respond")
def respond_to_bootcamp_information_request(
    conversation_id: str,
    payload: BootcampInformationRequestInput,
):
    conversation = BOOTCAMP_STORE.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Boot Camp conversation not found.")
    if not conversation["needsHandoff"] or conversation["status"] != "handoff":
        raise HTTPException(status_code=409, detail="This Boot Camp information request is already resolved.")

    latest_persona_message = next(
        (message for message in reversed(conversation["messages"]) if message.get("role") == "persona"),
        None,
    )
    if not latest_persona_message:
        raise HTTPException(status_code=409, detail="No simulated customer message is available to retry.")

    generated = generate_bootcamp_information_resolution(
        conversation["messages"],
        conversation["styleProfile"],
        payload.information,
    )
    knowledge_entry_id = f"bootcamp-{conversation_id}-{latest_persona_message['id']}"
    knowledge_source = save_learned_information(
        knowledge_entry_id,
        latest_persona_message["text"],
        payload.information,
        generated["knowledge_summary"],
    )
    BOOTCAMP_STORE.add_message(
        conversation_id,
        "tori",
        generated["customer_reply"],
        {
            "source": "information-request",
            "knowledgeSource": knowledge_source,
            "knowledgeSummary": generated["knowledge_summary"],
        },
    )
    BOOTCAMP_STORE.resolve_handoff(conversation_id)
    return {
        "status": "success",
        "conversation": BOOTCAMP_STORE.get_conversation(conversation_id),
        "knowledgeSource": knowledge_source,
        "knowledgeSummary": generated["knowledge_summary"],
    }


@app.post("/api/bootcamp/runs/{run_id}/control")
def control_bootcamp_run(run_id: str, payload: BootcampControlInput):
    run = BOOTCAMP_STORE.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Boot Camp run not found")
    operation = payload.operation.strip().lower()
    transitions = {"pause": "paused", "resume": "running", "stop": "stopped"}
    if operation not in transitions:
        raise HTTPException(status_code=400, detail="Use pause, resume, or stop")
    BOOTCAMP_STORE.update_run(run_id, transitions[operation])
    return BOOTCAMP_STORE.get_run(run_id)


@app.delete("/api/bootcamp/runs")
def reset_bootcamp_runs():
    latest = BOOTCAMP_STORE.latest_run()
    if latest and latest["status"] in {"running", "paused"}:
        raise HTTPException(status_code=409, detail="Stop the active run before resetting")
    BOOTCAMP_STORE.reset()
    return {"status": "reset"}


from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

frontend_dist = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi.json"):
            return None
        
        # Public landing page served at root "/"
        if not full_path or full_path == "":
            landing_path = os.path.join(frontend_dist, "landing.html")
            if os.path.exists(landing_path):
                return FileResponse(landing_path)

        file_path = os.path.join(frontend_dist, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8025))
    # Enable uvicorn auto-reload for local development
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

# Trigger reload: Aug 2 18:01

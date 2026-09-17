from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple, Union

try:
    from backend.curator.sanitizer import shared_knowledge_is_generic
except ImportError:
    try:
        from curator.sanitizer import shared_knowledge_is_generic
    except ImportError:
        def shared_knowledge_is_generic(text: str) -> bool:
            return True


TOKEN_RE: Pattern[str] = re.compile(r"[a-z0-9']+", re.IGNORECASE)

STYLE_STOP_WORDS: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "going", "had", "has", "have", "he", "her", "him", "his",
    "how", "i", "if", "in", "is", "it", "its", "just", "me", "my", "of", "ok",
    "okay", "on", "or", "our", "really", "she", "so", "sorry", "that", "then",
    "the", "their", "them", "there", "they", "this", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "will", "with", "you", "your",
}


def tokenise(text: str) -> list[str]:
    """Lightweight tokenizer with stopword stripping and plural normalization."""
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


CANONICAL_INTENT_TAXONOMY: Set[str] = {
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

STRICT_PLACEHOLDER_ALLOWLIST: Set[str] = {
    "website", "provider_name", "business_name", "street_address", "suburb",
    "state", "postcode", "business_phone", "email", "booking_arrival_notes",
    "booking_url", "phone", "deposit", "booking_id", "location", "date",
    "time", "address", "hotel_name", "room_number", "level_number", "building_number",
    "message", "knowledge", "slots", "current_time", "name", "service", "service_name", "price",
    "arrival_link",
    "line_key", "line_display_name", "line_provider_name", "line_information_url",
}

CRITICAL_UNRENDERED_TOKENS: List[str] = ["{website}", "{provider_name}", "{suburb}"]

STYLE_TEMPLATE_FALLBACKS: Dict[str, str] = {
    "service": "the relevant service",
    "service_name": "the relevant service",
    "price": "the current listed price",
    "date": "the requested date",
    "time": "a live calendar time",
}

UNRESOLVED_PLACEHOLDER_PATTERNS: List[Pattern[str]] = [
    re.compile(r"<UNMAPPED_[^>]+>", re.IGNORECASE),
    re.compile(r"\{booking_url\}", re.IGNORECASE),
    re.compile(r"\{unmapped_[^}]*\}", re.IGNORECASE),
    re.compile(r"<UNMAPPED>", re.IGNORECASE),
]

TEMPLATE_VARIABLE_PATTERN: Pattern[str] = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")


def validate_no_unresolved_placeholders(text: str, context_label: str = "prompt") -> None:
    """Strictly validate that no unresolved placeholders or unmapped tokens reach the LLM."""
    if not text:
        return
    for pattern in UNRESOLVED_PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match:
            raise ValueError(
                f"Pre-submission validation failed in {context_label}: Unresolved pattern '{match.group(0)}' found."
            )

    for token in CRITICAL_UNRENDERED_TOKENS:
        if token in text.lower():
            raise ValueError(
                f"Pre-submission validation failed in {context_label}: Unrendered token '{token}' found."
            )

    matches = re.findall(r"\{([a-zA-Z0-9_]+)\}", text)
    for m in matches:
        if m.lower() not in STRICT_PLACEHOLDER_ALLOWLIST:
            raise ValueError(
                f"Pre-submission validation failed in {context_label}: Unallowed placeholder '{{{m}}}' found."
            )


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
        self.path = Path(path)
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
        self._load_and_validate(self.path)

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
                self.validation_error = (
                    f"Line {line_num} (ID {record_id}): review_status must be 'approved', got '{review_status}'"
                )
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
                self.validation_error = (
                    f"Line {line_num} (ID {record_id}): Intent '{intent}' not in canonical taxonomy"
                )
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
                    self.validation_error = (
                        f"Line {line_num} (ID {record_id}): Unresolved placeholder '{match.group(0)}' found in text"
                    )
                    raise ValueError(self.validation_error)

            found_placeholders = re.findall(r"\{([a-zA-Z0-9_]+)\}", combined_text)
            for p_name in found_placeholders:
                if p_name.lower() not in STRICT_PLACEHOLDER_ALLOWLIST:
                    self.validation_status = "error"
                    self.validation_error = (
                        f"Line {line_num} (ID {record_id}): Placeholder '{{{p_name}}}' not in strict allowlist"
                    )
                    raise ValueError(self.validation_error)

            example_item = {
                "id": record_id,
                "intent": root_intent,
                "full_intent": str(intent),
                "user_text": user_text,
                "reply_text": reply_text,
                # The approved corpus predates line scoping and belongs to the
                # original primary SMS line. New examples can opt in to another
                # line (or both lines) with an explicit scope.
                "scope": str(record.get("scope", "primary")).strip().lower(),
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
        self,
        query: str,
        intent: Optional[str] = None,
        limit: int = 3,
        account_key: str = "primary",
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
            example_scope = self.examples[doc_id].get("scope")
            if example_scope not in {"shared", account_key}:
                continue
            if example_scope == "shared" and not shared_knowledge_is_generic(
                f"{self.examples[doc_id]['user_text']}\n{self.examples[doc_id]['reply_text']}"
            ):
                continue
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


def resolve_dataset_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve dataset path prioritizing explicit argument, then env var, then default backend path."""
    if path is not None:
        return Path(path)
    env_file = os.getenv("STYLE_EXAMPLES_FILE")
    if env_file:
        return Path(env_file)
    backend_dir = Path(__file__).resolve().parent.parent
    return backend_dir / "data" / "approved_intent_examples.jsonl"


DATASET_FILE = resolve_dataset_path()

STYLE_EXAMPLES_ENABLED = os.getenv("ENABLE_STYLE_EXAMPLES", "").strip().lower() in {
    "1", "true", "yes", "on"
}

example_index: Optional[SMSExampleIndex] = (
    SMSExampleIndex(DATASET_FILE) if STYLE_EXAMPLES_ENABLED else None
)

_SYNC_MODULE_NAMES = (
    "backend.main",
    "main",
    "backend.knowledge",
    "knowledge",
    "backend.knowledge.style_retrieval",
    "knowledge.style_retrieval",
    "style_retrieval",
)

_last_known_enabled: bool = STYLE_EXAMPLES_ENABLED
_last_known_index: Optional[SMSExampleIndex] = example_index


def set_style_examples_enabled(val: bool) -> None:
    """Set style examples enabled state symmetrically across all knowledge and main modules."""
    global STYLE_EXAMPLES_ENABLED, _last_known_enabled
    target_val = bool(val)
    STYLE_EXAMPLES_ENABLED = target_val
    _last_known_enabled = target_val
    import sys

    for mod_name in _SYNC_MODULE_NAMES:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            setattr(mod, "STYLE_EXAMPLES_ENABLED", target_val)


def set_example_index(idx: Optional[SMSExampleIndex]) -> None:
    """Set example index symmetrically across all knowledge and main modules."""
    global example_index, _last_known_index
    example_index = idx
    _last_known_index = idx
    import sys

    for mod_name in _SYNC_MODULE_NAMES:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            setattr(mod, "example_index", idx)


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


def render_style_examples(
    examples: list[tuple[str, str]],
    business_variables: Dict[str, Any],
) -> list[tuple[str, str]]:
    """Render permitted business variables inside retrieved style examples and strip unresolved placeholders."""
    if not examples:
        return []

    vars_map = dict(business_variables)
    for key, value in STYLE_TEMPLATE_FALLBACKS.items():
        vars_map.setdefault(key, value)
    website_val = vars_map.get("website") or vars_map.get("booking_url")
    if website_val:
        vars_map["website"] = website_val
        vars_map["booking_url"] = website_val

    if "phone" not in vars_map and "business_phone" in vars_map:
        vars_map["phone"] = vars_map["business_phone"]
    if "address" not in vars_map and "street_address" in vars_map:
        vars_map["address"] = vars_map["street_address"]
    if "information_url" not in vars_map and "line_information_url" in vars_map:
        vars_map["information_url"] = vars_map["line_information_url"]

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


def is_style_examples_enabled() -> bool:
    """Check whether style examples are enabled, synchronizing symmetrically across modules."""
    global STYLE_EXAMPLES_ENABLED, _last_known_enabled
    import sys

    target_val = _last_known_enabled
    changed = False

    for mod_name in _SYNC_MODULE_NAMES:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "STYLE_EXAMPLES_ENABLED"):
            val = bool(getattr(mod, "STYLE_EXAMPLES_ENABLED"))
            if val != _last_known_enabled:
                target_val = val
                changed = True
                break

    if not changed and STYLE_EXAMPLES_ENABLED != _last_known_enabled:
        target_val = STYLE_EXAMPLES_ENABLED
        changed = True

    if changed:
        _last_known_enabled = target_val
        STYLE_EXAMPLES_ENABLED = target_val
        for mod_name in _SYNC_MODULE_NAMES:
            mod = sys.modules.get(mod_name)
            if mod is not None:
                setattr(mod, "STYLE_EXAMPLES_ENABLED", target_val)

    return target_val


def get_example_index() -> Optional[SMSExampleIndex]:
    """Retrieve active SMSExampleIndex, synchronizing symmetrically across modules."""
    global example_index, _last_known_index
    import sys

    target_idx = _last_known_index
    changed = False

    for mod_name in _SYNC_MODULE_NAMES:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "example_index"):
            idx = getattr(mod, "example_index")
            if idx is not _last_known_index:
                target_idx = idx
                changed = True
                break

    if not changed and example_index is not _last_known_index:
        target_idx = example_index
        changed = True

    if changed:
        _last_known_index = target_idx
        example_index = target_idx
        for mod_name in _SYNC_MODULE_NAMES:
            mod = sys.modules.get(mod_name)
            if mod is not None:
                setattr(mod, "example_index", target_idx)

    return target_idx


def _resolve_business_variables(account_key: str) -> Dict[str, Any]:
    """Resolve provider business variables dynamically from main module if present."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "get_line_business_variable_values"):
            try:
                return mod.get_line_business_variable_values(account_key)
            except Exception:
                pass
    return {}


def get_style_examples(
    query: str,
    intent: Optional[str] = None,
    limit: int = 3,
    render_variables: bool = True,
    account_key: str = "primary",
    *,
    index: Optional[SMSExampleIndex] = None,
    enabled: Optional[bool] = None,
    business_variables: Optional[Dict[str, Any]] = None,
) -> list[tuple[str, str]]:
    """Return style examples matching query & intent within budget limits, with business variables rendered."""
    is_enabled = is_style_examples_enabled() if enabled is None else enabled
    if not is_enabled:
        return []
    idx = index if index is not None else get_example_index()
    if idx is None:
        return []
    if intent is None:
        intent = classify_query_intent(query)
    raw_examples = idx.search(
        query, intent=intent, limit=limit, account_key=account_key
    )
    if render_variables:
        resolved_vars = (
            business_variables
            if business_variables is not None
            else _resolve_business_variables(account_key)
        )
        return render_style_examples(raw_examples, resolved_vars)
    return raw_examples


__all__ = [
    "CANONICAL_INTENT_TAXONOMY",
    "CRITICAL_UNRENDERED_TOKENS",
    "DATASET_FILE",
    "SMSExampleIndex",
    "STRICT_PLACEHOLDER_ALLOWLIST",
    "STYLE_EXAMPLES_ENABLED",
    "STYLE_STOP_WORDS",
    "STYLE_TEMPLATE_FALLBACKS",
    "TEMPLATE_VARIABLE_PATTERN",
    "TOKEN_RE",
    "UNRESOLVED_PLACEHOLDER_PATTERNS",
    "classify_query_intent",
    "example_index",
    "get_example_index",
    "get_style_examples",
    "is_style_examples_enabled",
    "render_style_examples",
    "render_template_variables",
    "resolve_dataset_path",
    "set_example_index",
    "set_style_examples_enabled",
    "tokenise",
    "validate_no_unresolved_placeholders",
]

"""Staff Edit & Correction Classifier (Phase 7).

Governing Rules:
1. Classify staff edits into structured failure/correction categories:
   - style_only
   - missing_knowledge
   - stale_knowledge
   - incorrect_knowledge
   - wrong_scope
   - retrieval_failure
   - reasoning_failure
   - dynamic_data_failure
   - operator_preference
2. IMPORTANT: Only genuine knowledge corrections enter the business-knowledge pipeline.
   Tone/style/preference corrections and dynamic data adjustments must NEVER contaminate
   canonical business knowledge!
"""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .decision_trace import ResponderDecisionTrace

PRICE_PATTERN = re.compile(
    r"\$\s*\d+(?:\.\d{1,2})?|\b\d+\s*(?:dollars?|bucks?)\b|\b(?:price|cost|rate)\b[^\n]{0,20}\b\d+\b",
    re.IGNORECASE,
)

TIME_PATTERN = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b",
    re.IGNORECASE,
)

DATE_PATTERN = re.compile(
    r"\b(?:today|tomorrow|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
    re.IGNORECASE,
)

URL_PATTERN = re.compile(
    r"https?://[^\s)]+|www\.[^\s)]+",
    re.IGNORECASE,
)

GREETING_PATTERN = re.compile(
    r"^(?:hi|hello|hey|good\s+morning|good\s+afternoon|good\s+evening|g'day|dear|howdy)(?:\s+[a-z]+)?(?:[!,.\s]+|$)",
    re.IGNORECASE,
)

SIGNOFF_PATTERN = re.compile(
    r"(?:thanks(?:\s+for\s+understanding)?|thank\s+you(?:\s+for\s+understanding)?|cheers|regards|best\s+regards|best|xx+|have\s+a\s+(?:great|good)\s+day|let\s+(?:us|me)\s+know|talk\s+soon|see\s+you(?:\s+then)?|take\s+care)[!.\s]*$",
    re.IGNORECASE,
)

EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]",
    flags=re.UNICODE,
)

POLICY_KEYWORD_PATTERN = re.compile(
    r"\b(?:policy|require|required|must|bring|deposit|refund|refundable|cancellation|cancellations|parking|location|entry|entrance|stairs|elevator|id|photo\s+id|rules?|allowed|prohibited|forbidden|notice|terms)\b",
    re.IGNORECASE,
)

TONE_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "so", "as", "at",
    "by", "for", "from", "in", "into", "of", "off", "on", "onto", "out",
    "over", "to", "up", "with", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "can", "could",
    "cannot", "can't", "dont", "don't", "wont", "won't",
    "will", "would", "shall", "should", "may", "might", "must", "i",
    "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "this", "that",
    "these", "those", "here", "there", "just", "please", "thanks", "thank",
    "cheers", "hi", "hello", "hey", "well", "okay", "ok", "yeah", "yes", "no",
    "unfortunately", "understanding", "sorry", "apologies", "appreciate",
    "patience", "regards", "warmly", "sincerely", "studio", "business", "clinic",
    "team", "staff", "management"
}


class EditClassification(str, Enum):
    """Classification taxonomy for human corrections on AI draft responses."""

    STYLE_ONLY = "style_only"
    MISSING_KNOWLEDGE = "missing_knowledge"
    STALE_KNOWLEDGE = "stale_knowledge"
    INCORRECT_KNOWLEDGE = "incorrect_knowledge"
    WRONG_SCOPE = "wrong_scope"
    RETRIEVAL_FAILURE = "retrieval_failure"
    REASONING_FAILURE = "reasoning_failure"
    DYNAMIC_DATA_FAILURE = "dynamic_data_failure"
    OPERATOR_PREFERENCE = "operator_preference"


@dataclass
class EditClassificationResult:
    """Structured result of classifying a staff correction."""

    classification: EditClassification
    confidence: float
    is_knowledge_candidate: bool
    reason: str
    diff_summary: Optional[Dict[str, Any]] = None
    extracted_knowledge_snippet: Optional[str] = None
    detected_dynamic_elements: Optional[List[str]] = None


def _extract_dynamic_elements(text: str) -> Dict[str, List[str]]:
    """Extract price, time, date, and URL tokens from text."""
    return {
        "prices": [m.strip() for m in PRICE_PATTERN.findall(text or "")],
        "times": [m.strip() for m in TIME_PATTERN.findall(text or "")],
        "dates": [m.strip() for m in DATE_PATTERN.findall(text or "")],
        "urls": [m.strip() for m in URL_PATTERN.findall(text or "")],
    }


def _strip_greetings_and_signoffs(text: str) -> str:
    """Strip greetings, signoffs, and emojis to isolate core semantic body."""
    t = EMOJI_PATTERN.sub("", text)
    lines = [line.strip() for line in t.splitlines() if line.strip()]
    if not lines:
        return ""

    # Strip greeting from first line
    lines[0] = GREETING_PATTERN.sub("", lines[0]).strip()
    if not lines[0] and len(lines) > 1:
        lines = lines[1:]

    if not lines:
        return ""

    # Strip signoff from last line
    lines[-1] = SIGNOFF_PATTERN.sub("", lines[-1]).strip()
    if not lines[-1] and len(lines) > 1:
        lines = lines[:-1]

    return " ".join(lines).strip()


def _tokenize_substantive_words(text: str) -> Set[str]:
    """Tokenize words excluding common stop/filler words and punctuation."""
    words = re.findall(r"\b[a-zA-Z0-9']+\b", text.lower())
    return {w for w in words if w not in TONE_STOP_WORDS and len(w) > 1}


def classify_human_correction(
    original_draft: str,
    staff_edited_text: str,
    trace: Optional[ResponderDecisionTrace] = None,
) -> EditClassificationResult:
    """Classify the difference between original AI draft and human staff edit.
    
    Guarantees:
    - Purely stylistic/greeting changes classified as style_only / operator_preference
      and NEVER produce a business knowledge candidate.
    - Dynamic data changes (prices, dates, times, URLs) are classified as
      dynamic_data_failure and NEVER produce a business knowledge candidate.
    - Factual additions, policy additions, or corrections of existing knowledge
      are classified as missing_knowledge, stale_knowledge, or incorrect_knowledge
      and produce knowledge candidates.
    """
    draft = (original_draft or "").strip()
    edited = (staff_edited_text or "").strip()

    # 1. Exact match / No change
    if draft == edited:
        return EditClassificationResult(
            classification=EditClassification.STYLE_ONLY,
            confidence=1.0,
            is_knowledge_candidate=False,
            reason="Original draft and edited text are identical",
            diff_summary={"added": [], "removed": []},
        )

    # 2. Extract dynamic tokens from both
    draft_dyn = _extract_dynamic_elements(draft)
    edited_dyn = _extract_dynamic_elements(edited)

    dyn_added = []
    dyn_changed = False
    all_detected_dyn = []

    for category in ("prices", "times", "dates", "urls"):
        d_items = [i.lower() for i in draft_dyn[category]]
        e_items = [i.lower() for i in edited_dyn[category]]
        all_detected_dyn.extend(edited_dyn[category])
        if d_items != e_items:
            dyn_changed = True
            diff_items = [x for x in edited_dyn[category] if x.lower() not in d_items]
            if diff_items:
                dyn_added.extend(diff_items)

    # 3. Check diff lines & tokens
    diff = list(difflib.ndiff(draft.splitlines(), edited.splitlines()))
    added_lines = [line[2:].strip() for line in diff if line.startswith("+ ") and line[2:].strip()]
    removed_lines = [line[2:].strip() for line in diff if line.startswith("- ") and line[2:].strip()]

    # 4. If dynamic data changed and this is the dominant alteration
    if dyn_changed:
        # Check if the remaining substantive text without dynamic data is largely similar
        draft_no_dyn = PRICE_PATTERN.sub("", TIME_PATTERN.sub("", DATE_PATTERN.sub("", URL_PATTERN.sub("", draft))))
        edited_no_dyn = PRICE_PATTERN.sub("", TIME_PATTERN.sub("", DATE_PATTERN.sub("", URL_PATTERN.sub("", edited))))

        core_draft = _strip_greetings_and_signoffs(draft_no_dyn)
        core_edited = _strip_greetings_and_signoffs(edited_no_dyn)

        words_d = _tokenize_substantive_words(core_draft)
        words_e = _tokenize_substantive_words(core_edited)

        overlap = len(words_d & words_e)
        union = len(words_d | words_e)
        jaccard = overlap / union if union > 0 else 1.0

        new_words = words_e - words_d
        has_policy_words = any(POLICY_KEYWORD_PATTERN.search(w) for w in new_words)

        if not has_policy_words or jaccard > 0.35:
            return EditClassificationResult(
                classification=EditClassification.DYNAMIC_DATA_FAILURE,
                confidence=0.92,
                is_knowledge_candidate=False,
                reason=f"Altered dynamic data values: {', '.join(dyn_added or all_detected_dyn)}",
                diff_summary={"added": added_lines, "removed": removed_lines},
                detected_dynamic_elements=all_detected_dyn,
            )

    # 5. Check if changes are purely style, greetings, sign-offs, or punctuation
    clean_draft = _strip_greetings_and_signoffs(draft)
    clean_edited = _strip_greetings_and_signoffs(edited)

    tokens_draft = _tokenize_substantive_words(clean_draft)
    tokens_edited = _tokenize_substantive_words(clean_edited)

    if tokens_draft == tokens_edited:
        # Pure greeting, sign-off, punctuation, or emoji change
        return EditClassificationResult(
            classification=EditClassification.STYLE_ONLY,
            confidence=0.98,
            is_knowledge_candidate=False,
            reason="Modifications were limited to greetings, sign-offs, emojis, or punctuation",
            diff_summary={"added": added_lines, "removed": removed_lines},
        )

    # Check substantive word difference
    added_substantive = tokens_edited - tokens_draft
    removed_substantive = tokens_draft - tokens_edited

    # 6. Check for wrong account/scope if trace is present
    if trace and trace.account_key:
        other_line = "secondary" if trace.account_key == "primary" else "primary"
        if other_line.lower() in edited.lower() and other_line.lower() not in draft.lower():
            return EditClassificationResult(
                classification=EditClassification.WRONG_SCOPE,
                confidence=0.88,
                is_knowledge_candidate=False,
                reason="Staff corrected provider/business scope in response",
                diff_summary={"added": added_lines, "removed": removed_lines},
            )

    # 7. Check if trace has relevant knowledge context
    if trace:
        winning_record = trace.winning_record_id
        had_conflict = trace.had_conflict
        retrieved = trace.retrieved_candidates

        # If retrieved candidates already contained what staff added
        if retrieved and added_substantive:
            candidate_texts = " ".join([
                str(c.get("content") or c.get("text") or "") for c in retrieved
            ]).lower()
            matched_candidate_tokens = {w for w in added_substantive if w in candidate_texts}
            if len(matched_candidate_tokens) >= max(1, len(added_substantive) * 0.5):
                if not winning_record or had_conflict:
                    return EditClassificationResult(
                        classification=EditClassification.RETRIEVAL_FAILURE,
                        confidence=0.85,
                        is_knowledge_candidate=False,
                        reason="Required knowledge was present in candidate pool but retrieval failed to select it",
                        diff_summary={"added": added_lines, "removed": removed_lines},
                    )
                else:
                    return EditClassificationResult(
                        classification=EditClassification.REASONING_FAILURE,
                        confidence=0.82,
                        is_knowledge_candidate=False,
                        reason="Authoritative knowledge was retrieved but reasoning/generation omitted or misapplied it",
                        diff_summary={"added": added_lines, "removed": removed_lines},
                    )

        # If winning record was used, but staff explicitly contradicted/corrected it
        if winning_record and removed_substantive and added_substantive:
            return EditClassificationResult(
                classification=EditClassification.INCORRECT_KNOWLEDGE,
                confidence=0.87,
                is_knowledge_candidate=True,
                reason="Staff corrected factual statement produced from retrieved active knowledge",
                diff_summary={"added": added_lines, "removed": removed_lines},
                extracted_knowledge_snippet=" ".join(added_lines) if added_lines else clean_edited,
            )

    # Check if the existing core assertion was simply rephrased with polite/tone words
    overlap = len(tokens_draft & tokens_edited)
    has_policy_indicator = any(POLICY_KEYWORD_PATTERN.search(w) for w in added_substantive)

    if tokens_draft and (tokens_draft.issubset(tokens_edited) or (overlap / len(tokens_draft) >= 0.7)):
        if not has_policy_indicator or len(added_substantive) <= 2:
            return EditClassificationResult(
                classification=EditClassification.OPERATOR_PREFERENCE,
                confidence=0.88,
                is_knowledge_candidate=False,
                reason="Rephrased existing draft response with tone/operator preference",
                diff_summary={"added": added_lines, "removed": removed_lines},
            )

    # 8. Check if substantive new knowledge/policy rules were introduced
    if added_substantive and (has_policy_indicator or len(added_substantive) >= 3):
        # Extract new factual snippet
        snippet = " ".join(added_lines) if added_lines else clean_edited
        return EditClassificationResult(
            classification=EditClassification.MISSING_KNOWLEDGE,
            confidence=0.90,
            is_knowledge_candidate=True,
            reason="Staff added substantive business policy or informational facts missing from draft",
            diff_summary={"added": added_lines, "removed": removed_lines},
            extracted_knowledge_snippet=snippet,
        )

    # 9. Minor phrasing or tone adjustment
    if len(added_substantive) <= 2 and not has_policy_indicator:
        return EditClassificationResult(
            classification=EditClassification.OPERATOR_PREFERENCE,
            confidence=0.85,
            is_knowledge_candidate=False,
            reason="Minor phrasing or wording adjustment reflecting operator tone preference",
            diff_summary={"added": added_lines, "removed": removed_lines},
        )

    # Default fallback: If new substantive content was added
    if added_substantive:
        return EditClassificationResult(
            classification=EditClassification.MISSING_KNOWLEDGE,
            confidence=0.75,
            is_knowledge_candidate=True,
            reason="Staff introduced additional content not present in the draft",
            diff_summary={"added": added_lines, "removed": removed_lines},
            extracted_knowledge_snippet=" ".join(added_lines) if added_lines else clean_edited,
        )

    return EditClassificationResult(
        classification=EditClassification.OPERATOR_PREFERENCE,
        confidence=0.80,
        is_knowledge_candidate=False,
        reason="Phrasing adjustment without business knowledge additions",
        diff_summary={"added": added_lines, "removed": removed_lines},
    )

import os
import json
import re
import pytest
from pathlib import Path
from collections import Counter

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from main import CANONICAL_INTENT_TAXONOMY, DATASET_FILE

RAW_PII_PATTERNS = [
    re.compile(r"\b04\d{2}[-\s]?\d{3}[-\s]?\d{3}\b"),
    re.compile(r"\b\+61\s?4\d{2}[-\s]?\d{3}[-\s]?\d{3}\b"),
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
]

UNMAPPED_TOKEN_PATTERNS = [
    re.compile(r"<UNMAPPED_[^>]+>", re.IGNORECASE),
    re.compile(r"<UNMAPPED>", re.IGNORECASE),
    re.compile(r"\{unmapped_[^}]*\}", re.IGNORECASE),
]

BOOKING_URL_PATTERN = re.compile(r"\{booking_url\}", re.IGNORECASE)

ROOM_AND_HOTEL_PATTERNS = [
    re.compile(r"\b(?:room|rm|suite|apt|unit)\s*#?\s*\d+\b", re.IGNORECASE),
    re.compile(
        r"\b(?:Hilton|Marriott|Hyatt|Sheraton|Novotel|Ibis|Rydges|InterContinental|Crown|Meriton|Shangri-La|Westin|Four Seasons|Sofitel)\b",
        re.IGNORECASE,
    ),
]


def validate_dataset_file(
    file_path: Path,
    min_intent_count: int = 10,
    max_intent_count: int = 20,
    exact_count_per_intent: int | None = None,
) -> dict:
    if not file_path.exists():
        raise FileNotFoundError(f"Dataset file not found: {file_path}")

    lines = file_path.read_text(encoding="utf-8").splitlines()
    seen_ids = set()
    intent_counts = Counter()
    validated_records = []

    for line_num, line in enumerate(lines, 1):
        line_str = line.strip()
        if not line_str:
            continue

        try:
            record = json.loads(line_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Line {line_num}: Invalid JSON format: {e}") from e

        record_id = str(record.get("id", "")).strip()
        if not record_id:
            raise ValueError(f"Line {line_num}: Missing 'id'")
        if record_id in seen_ids:
            raise ValueError(f"Line {line_num}: Duplicate ID '{record_id}'")
        seen_ids.add(record_id)

        review_status = str(record.get("review_status", "")).strip()
        if review_status != "approved":
            raise ValueError(
                f"Line {line_num} (ID {record_id}): review_status must be 'approved', got '{review_status}'"
            )

        intent = record.get("intent") or record.get("primary_intent")
        if not intent:
            raise ValueError(f"Line {line_num} (ID {record_id}): Missing intent field")
        root_intent = str(intent).split(":")[0].strip().lower()
        if root_intent not in CANONICAL_INTENT_TAXONOMY:
            raise ValueError(
                f"Line {line_num} (ID {record_id}): Intent '{intent}' not in canonical taxonomy"
            )

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
            raise ValueError(f"Line {line_num} (ID {record_id}): Empty user or reply text")

        combined_text = f"{user_text} {reply_text}"

        for pattern in RAW_PII_PATTERNS:
            if pattern.search(combined_text):
                raise ValueError(f"Line {line_num} (ID {record_id}): Raw PII detected in text")

        for pattern in UNMAPPED_TOKEN_PATTERNS:
            match = pattern.search(combined_text)
            if match:
                raise ValueError(
                    f"Line {line_num} (ID {record_id}): Unmapped token '{match.group(0)}' found"
                )

        if BOOKING_URL_PATTERN.search(combined_text):
            raise ValueError(
                f"Line {line_num} (ID {record_id}): Deprecated {{booking_url}} found, must use {{website}}"
            )

        for pattern in ROOM_AND_HOTEL_PATTERNS:
            match = pattern.search(combined_text)
            if match:
                raise ValueError(
                    f"Line {line_num} (ID {record_id}): Forbidden room number or hotel name '{match.group(0)}' found"
                )

        if len(user_text) > 1500:
            raise ValueError(
                f"Line {line_num} (ID {record_id}): User text length ({len(user_text)}) exceeds limit of 1500 chars"
            )
        if len(reply_text) > 1500:
            raise ValueError(
                f"Line {line_num} (ID {record_id}): Reply text length ({len(reply_text)}) exceeds limit of 1500 chars"
            )

        intent_counts[root_intent] += 1
        validated_records.append(record)

    for canonical_intent in CANONICAL_INTENT_TAXONOMY:
        count = intent_counts.get(canonical_intent, 0)
        if exact_count_per_intent is not None:
            if count != exact_count_per_intent:
                raise ValueError(
                    f"Intent '{canonical_intent}' count is {count}, expected exactly {exact_count_per_intent}"
                )
        else:
            if count < min_intent_count or count > max_intent_count:
                raise ValueError(
                    f"Intent '{canonical_intent}' count is {count}, outside allowed range [{min_intent_count}, {max_intent_count}]"
                )

    return {
        "total_records": len(validated_records),
        "intent_counts": dict(intent_counts),
        "status": "valid",
    }


def test_approved_intent_examples_dataset():
    """Verify production approved_intent_examples.jsonl passes all dataset validation rules."""
    res = validate_dataset_file(DATASET_FILE, exact_count_per_intent=15)
    assert res["status"] == "valid"
    assert res["total_records"] == 180
    assert len(res["intent_counts"]) == 12
    for intent, count in res["intent_counts"].items():
        assert count == 15


def test_validation_invalid_json(tmp_path):
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text("NOT_JSON\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON format"):
        validate_dataset_file(bad_file)


def test_validation_duplicate_ids(tmp_path):
    dup_file = tmp_path / "dup.jsonl"
    rec1 = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "pricing", "incoming": "Rate?", "reply": "100"})
    rec2 = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "pricing", "incoming": "Rate 2?", "reply": "200"})
    dup_file.write_text(f"{rec1}\n{rec2}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate ID"):
        validate_dataset_file(dup_file)


def test_validation_unapproved_status(tmp_path):
    unapp_file = tmp_path / "unapproved.jsonl"
    rec = json.dumps({"id": "ex_1", "review_status": "pending", "intent": "pricing", "incoming": "Rate?", "reply": "100"})
    unapp_file.write_text(f"{rec}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="review_status must be 'approved'"):
        validate_dataset_file(unapp_file)


def test_validation_invalid_intent(tmp_path):
    bad_intent_file = tmp_path / "bad_intent.jsonl"
    rec = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "invalid_intent_xyz", "incoming": "Rate?", "reply": "100"})
    bad_intent_file.write_text(f"{rec}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not in canonical taxonomy"):
        validate_dataset_file(bad_intent_file)


def test_validation_raw_pii(tmp_path):
    pii_file = tmp_path / "pii.jsonl"
    rec = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "pricing", "incoming": "Call me at 0412 345 678", "reply": "Sure"})
    pii_file.write_text(f"{rec}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Raw PII detected"):
        validate_dataset_file(pii_file)


def test_validation_unmapped_tokens(tmp_path):
    unmapped_file = tmp_path / "unmapped.jsonl"
    rec = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "pricing", "incoming": "Rate?", "reply": "Check <UNMAPPED_URL>"})
    unmapped_file.write_text(f"{rec}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unmapped token"):
        validate_dataset_file(unmapped_file)


def test_validation_booking_url(tmp_path):
    burl_file = tmp_path / "burl.jsonl"
    rec = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "pricing", "incoming": "Rate?", "reply": "Check {booking_url}"})
    burl_file.write_text(f"{rec}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Deprecated {booking_url} found"):
        validate_dataset_file(burl_file)


def test_validation_room_numbers_and_hotels(tmp_path):
    hotel_file = tmp_path / "hotel.jsonl"
    rec = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "location_or_arrival", "incoming": "Where to?", "reply": "I am at Hilton Room 302"})
    hotel_file.write_text(f"{rec}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Forbidden room number or hotel name"):
        validate_dataset_file(hotel_file)


def test_validation_length_limits(tmp_path):
    len_file = tmp_path / "len.jsonl"
    rec = json.dumps({"id": "ex_1", "review_status": "approved", "intent": "pricing", "incoming": "Hi", "reply": "A" * 1501})
    len_file.write_text(f"{rec}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Reply text length"):
        validate_dataset_file(len_file)


def test_validation_per_intent_count_limits(tmp_path):
    count_file = tmp_path / "count.jsonl"
    recs = []
    # Write only 5 examples for availability, violating min limit 10
    for i in range(5):
        recs.append(json.dumps({"id": f"ex_{i}", "review_status": "approved", "intent": "availability", "incoming": "Free?", "reply": "Yes"}))
    count_file.write_text("\n".join(recs) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside allowed range"):
        validate_dataset_file(count_file, min_intent_count=10, max_intent_count=20)

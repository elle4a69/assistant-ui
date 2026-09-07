import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import main


NOW = datetime(2030, 1, 15, 12, 0, 0)


def record(record_id, key="policy", text="Use the approved wording.", **overrides):
    value = {
        "id": record_id,
        "canonical_key": key,
        "text": text,
        "sms_account_key": "primary",
        "source_type": "manual_guidance",
        "created_at": "2030-01-01T00:00:00Z",
        "updated_at": "2030-01-01T00:00:00Z",
        "status": "active",
        "revision": 1,
        "review_status": "approved",
        "retrieval_enabled": True,
    }
    value.update(overrides)
    return value


def selected(records, account="primary"):
    return main.resolve_knowledge_authority(records, account_key=account, now=NOW)


def test_legacy_record_normalization_is_stable_and_non_destructive():
    source = {"text": "A legacy durable policy."}
    one = main.normalize_knowledge_record(source, source="legacy.jsonl", source_index=4)
    two = main.normalize_knowledge_record(source, source="legacy.jsonl", source_index=4)
    assert source == {"text": "A legacy durable policy."}
    assert one["id"] == two["id"]
    assert one["canonical_key"]
    assert one["sms_account_key"] == "internal"
    assert one["status"] == "quarantined"
    assert one["review_status"] == "pending"
    assert one["retrieval_enabled"] is False
    assert selected([one]) == []


def test_new_revision_supersedes_old_revision_deterministically():
    old = record("old", text="Original policy.")
    new = record("new", text="Revised policy.", supersedes_id="old", revision=2,
                 updated_at="2030-01-02T00:00:00Z")
    assert [item["id"] for item in selected([old, new])] == ["new"]


def test_expired_and_future_dated_records_are_excluded():
    expired = record("expired", effective_until="2030-01-14T12:00:00Z")
    future = record("future", effective_from="2030-01-16T12:00:00Z")
    assert selected([expired, future]) == []


def test_effective_dates_convert_offsets_to_utc_before_comparison():
    # 20:00 at UTC+10 is 10:00 UTC, so it is already effective at NOW.
    offset_record = record("offset", effective_from="2030-01-15T20:00:00+10:00")
    assert [item["id"] for item in selected([offset_record])] == ["offset"]


def test_unapproved_and_quarantined_records_are_excluded():
    pending = record("pending", review_status="pending")
    quarantined = record("quarantined", status="quarantined")
    assert selected([pending, quarantined]) == []


def test_invalid_and_cyclic_supersession_chains_fail_closed():
    dangling = record("dangling", supersedes_id="missing")
    first = record("first", supersedes_id="second")
    second = record("second", supersedes_id="first", revision=2)
    assert selected([dangling, first, second]) == []


def test_cross_account_supersession_fails_closed():
    old = record("primary-old")
    other_line = record("secondary-new", sms_account_key="secondary", supersedes_id="primary-old", revision=2)
    assert selected([old, other_line], "primary") == []
    assert selected([old, other_line], "secondary") == []


def test_cross_topic_supersession_fails_closed():
    policy = record("policy", key="cancellation")
    unrelated = record("unrelated", key="arrival", supersedes_id="policy", revision=2)
    assert selected([policy, unrelated]) == []


def test_cross_topic_supersession_invalidates_every_descendant():
    policy = record("policy", key="cancellation")
    unrelated = record("unrelated", key="arrival", supersedes_id="policy", revision=2)
    leaf = record("leaf", key="arrival", supersedes_id="unrelated", revision=3)
    assert selected([policy, unrelated, leaf]) == []


def test_competing_successors_are_a_conflict_not_a_highest_revision_choice():
    old = record("old", key="policy")
    first = record("first", key="policy", text="First replacement.", supersedes_id="old", revision=2)
    second = record("second", key="policy", text="Second replacement.", supersedes_id="old", revision=3)
    assert selected([old, first, second]) == []
    assert any(item["code"] == "knowledge_excluded_conflict" for item in main.knowledge_authority_reason_codes())


def test_competing_successors_invalidate_descendants_of_each_branch():
    old = record("old", key="policy")
    left = record("left", key="policy", text="Left replacement.", supersedes_id="old", revision=2)
    right = record("right", key="policy", text="Right replacement.", supersedes_id="old", revision=3)
    left_leaf = record("left-leaf", key="policy", text="Left leaf.", supersedes_id="left", revision=4)
    assert selected([old, left, right, left_leaf]) == []


def test_conflicting_active_records_are_excluded_from_customer_context(monkeypatch):
    first = record("first", key="cancellation", text="Seven days notice.")
    second = record("second", key="cancellation", text="Twenty four hours notice.")
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [first, second])
    assert main.retrieve_knowledge_chunks("notice", account_key="primary") == []
    assert any(item["code"] == "knowledge_excluded_conflict" for item in main.knowledge_authority_reason_codes())


def test_sms_account_isolation_is_strict_for_account_bound_records(monkeypatch):
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [
        record("primary", text="Primary policy.", sms_account_key="primary"),
        record("secondary", text="Secondary policy.", sms_account_key="secondary"),
    ])
    assert [item["id"] for item in main.retrieve_knowledge_chunks("policy", account_key="primary")] == ["primary"]
    assert [item["id"] for item in main.retrieve_knowledge_chunks("policy", account_key="secondary")] == ["secondary"]


def test_current_catalogue_overrides_stale_learned_price_and_duration(tmp_path, monkeypatch):
    (tmp_path / "line_1_services.json").write_text(json.dumps([
        {"id": "current", "name": "Current service", "price": 250, "duration": 75},
    ]), encoding="utf-8")
    (tmp_path / "line_2_services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [
        record("stale", key="current-service", text="Current service is $100 for 30 minutes."),
    ])
    context = main.build_business_context("price", account_key="primary")
    assert "$100" not in context
    assert "Price: $250" in context
    assert "Duration: 75 minutes" in context


def test_booking_proposal_resolves_current_account_catalogue_at_decision_time(tmp_path, monkeypatch):
    (tmp_path / "line_1_services.json").write_text(json.dumps([
        {"id": "service", "name": "New service", "price": 275, "duration": 75},
    ]), encoding="utf-8")
    (tmp_path / "line_2_services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "booking_availability_error", lambda *_args: None)
    thread = SimpleNamespace(sms_account_key="primary", customer_phone="+61412345678")
    proposal = main.propose_conversational_booking(
        thread, service_id="service", start_time="2030-01-20T09:15:00",
        customer_name="Ben", notes=None,
    )["proposal"]
    assert proposal["service_name"] == "New service"
    assert proposal["price"] == 275
    assert proposal["duration"] == 75


def test_availability_is_never_sourced_from_knowledge(monkeypatch):
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [
        record("availability", text="I am available at 9:15 today."),
    ])
    context = main.build_authority_context("Are you free at 9:15?", "primary", booking_or_availability=True)
    assert "available at 9:15" not in context
    assert "live calendar only" in context.casefold()


def test_name_only_follow_up_preserves_accepted_915_slot():
    messages = [
        SimpleNamespace(role="agent", text="I can do 9:15 tomorrow."),
        SimpleNamespace(role="agent", text="What name should I put the booking under?"),
        SimpleNamespace(role="customer", text="Ben"),
    ]
    state = main.name_only_follow_up_preserves_slot(messages, "primary", "Ben")
    assert state["accepted_slot"] == "9:15"
    assert state["resolution"] == "preserve_pending_slot"


def test_old_name_question_cannot_revive_a_slot_for_a_much_later_name_only_message():
    asked_at = datetime(2030, 1, 10, 9, 0)
    messages = [
        SimpleNamespace(role="agent", text="I can do 9:15 tomorrow.", at=asked_at),
        SimpleNamespace(role="agent", text="What name should I put the booking under?", at=asked_at + timedelta(minutes=1)),
        SimpleNamespace(role="customer", text="Ben", at=asked_at + timedelta(days=1)),
    ]
    assert main.name_only_follow_up_preserves_slot(messages, "primary", "Ben") is None


def test_unrelated_late_name_exchange_cannot_recover_an_old_clock_time():
    old_at = NOW - timedelta(days=10)
    current_at = NOW
    messages = [
        SimpleNamespace(role="agent", text="I can do 9:15 tomorrow.", at=old_at),
        SimpleNamespace(role="customer", text="Thanks, I will think about it.", at=old_at + timedelta(minutes=1)),
        SimpleNamespace(role="agent", text="What name should I put the booking under?", at=current_at),
        SimpleNamespace(role="customer", text="Ben", at=current_at + timedelta(minutes=1)),
    ]
    assert main.name_only_follow_up_preserves_slot(messages, "primary", "Ben") is None


def test_fresh_calendar_conflict_is_explicitly_distinguished_from_name_follow_up():
    messages = [
        SimpleNamespace(role="agent", text="I can do 9:15 tomorrow."),
        SimpleNamespace(role="agent", text="What name should I put the booking under?"),
    ]
    state = main.name_only_follow_up_preserves_slot(
        messages, "primary", "Ben", fresh_calendar_conflict=True,
    )
    assert state["accepted_slot"] == "9:15"
    assert state["resolution"] == "fresh_calendar_conflict"


def test_expired_successor_does_not_resurrect_older_fact():
    old = record("old", text="Old policy.")
    expired_successor = record(
        "new", text="New policy.", supersedes_id="old", revision=2,
        effective_until="2030-01-14T00:00:00Z",
    )
    assert selected([old, expired_successor]) == []


def test_editing_a_learned_record_increments_its_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    path = tmp_path / main.LEARNED_INFORMATION_FILENAME
    path.write_text(json.dumps(record("editable", revision=4)) + "\n", encoding="utf-8")
    updated = main.replace_learned_information_entry("editable", {"text": "Edited durable policy."})
    assert updated["revision"] == 5
    assert updated["version"] == 5
    assert updated["status"] == "quarantined"
    assert updated["review_status"] == "pending"
    assert updated["retrieval_enabled"] is False
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["revision"] == 5
    assert saved["version"] == 5
    assert saved["status"] == "quarantined"
    assert saved["review_status"] == "pending"
    assert saved["retrieval_enabled"] is False

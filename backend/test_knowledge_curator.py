import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main


NOW = datetime(2030, 1, 15, 12, 0, 0)


def record(record_id, key="policy", text="Use the approved wording.", **overrides):
    item = {
        "id": record_id,
        "canonical_key": key,
        "text": text,
        "sms_account_key": "primary",
        "scope": "primary",
        "source_type": "manual_guidance",
        "applies_when": "When the customer asks about the service policy.",
        "created_at": "2030-01-01T00:00:00Z",
        "updated_at": "2030-01-01T00:00:00Z",
        "status": "active",
        "revision": 1,
        "version": 1,
        "review_status": "approved",
        "retrieval_enabled": True,
    }
    item.update(overrides)
    return item


def finding_types(records):
    return {item["finding_type"] for item in main.inspect_knowledge_integrity(records, now=NOW)}


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([record("a"), record("b")], "exact_duplicate"),
        ([record("a", text="First rule."), record("b", text="Different rule.")], "incompatible_active_records"),
        ([record("a", effective_until="2030-01-14T00:00:00Z")], "expired_record"),
        ([record("a", effective_from="2030-01-16T00:00:00Z")], "future_record"),
        ([record("a", supersedes_id="missing")], "dangling_supersession"),
        ([record("a", supersedes_id="b"), record("b", supersedes_id="a")], "cyclic_supersession"),
        ([record("a", key="one"), record("b", key="two", supersedes_id="a")], "cross_topic_supersession"),
        ([record("a"), record("b", scope="secondary", sms_account_key="secondary", supersedes_id="a")], "cross_scope_supersession"),
        ([record("a"), record("b", supersedes_id="a"), record("c", supersedes_id="a")], "branched_supersession"),
        ([record("a", status="not-a-status")], "invalid_metadata"),
        ([record("a", canonical_key="")], "invalid_metadata"),
        ([record("a", text="This service costs $100 for 30 minutes.")], "literal_dynamic_authority"),
        ([record("a", revision=1), record("b", revision=2, text="Updated wording.", retrieval_enabled=False, review_status="pending", status="quarantined")], "apparently_superseded"),
        ([record("a", scope="internal", sms_account_key="internal", retrieval_enabled=False, status="quarantined", review_status="pending")], "owner_answer_required"),
    ],
)
def test_deterministic_audit_detects_each_finding_class(records, expected):
    assert expected in finding_types(records)


def curator_paths(tmp_path, monkeypatch, records):
    knowledge = tmp_path / "knowledge"
    data = tmp_path / "data"
    knowledge.mkdir()
    data.mkdir()
    (knowledge / main.LEARNED_INFORMATION_FILENAME).write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_STATE_PATH", str(data / "curator.json"))
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [])
    monkeypatch.setattr(main, "openai_client", None)
    return knowledge, data


def test_curator_rerun_is_idempotent_and_state_is_content_free(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    first = main.run_knowledge_curator()
    second = main.run_knowledge_curator()
    assert first["run"]["created_proposals"] >= 1
    assert second["run"]["created_proposals"] == 0
    state = main.get_knowledge_curator_state()
    unresolved = [item for item in state["proposals"] if item["status"] == "proposed"]
    assert len({item["fingerprint"] for item in unresolved}) == len(unresolved)
    serialized = (data / "curator.json").read_text(encoding="utf-8")
    assert "The service costs $100" not in serialized
    assert "prompt" not in serialized.casefold()


def test_manual_curator_reports_quota_exhaustion_without_losing_deterministic_findings(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])

    class FailingResponses:
        def create(self, **_kwargs):
            error = RuntimeError("provider detail must remain hidden")
            error.code = "insufficient_quota"
            error.body = {"error": {"code": "insufficient_quota"}}
            raise error

    monkeypatch.setattr(main, "openai_client", SimpleNamespace(responses=FailingResponses()))
    result = main.run_knowledge_curator()
    assert result["run"]["status"] == "completed_with_warning"
    assert result["run"]["error_code"] == "openai_quota_exhausted"
    assert result["proposals"]
    assert "provider detail" not in json.dumps(main.get_knowledge_curator_state())


def test_model_receives_metadata_not_knowledge_text():
    calls = []

    class CapturingResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text='{"proposals": []}')

    original = main.openai_client
    main.openai_client = SimpleNamespace(responses=CapturingResponses())
    try:
        findings = main.inspect_knowledge_integrity([record("price", text="Sensitive wording costs $100.")], now=NOW)
        main._curator_enrich_proposals(findings)
    finally:
        main.openai_client = original
    assert calls
    assert "Sensitive wording" not in calls[0]["input"]
    assert "record_refs" in calls[0]["input"]


def test_accept_creates_only_a_quarantined_pending_draft_then_final_approval_is_atomic(tmp_path, monkeypatch):
    knowledge, _ = curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    result = main.run_knowledge_curator()
    proposal = next(item for item in result["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    accepted = main.accept_knowledge_curator_proposal(proposal["id"])
    entries = {item["id"]: item for item in main.list_learned_information()}
    draft = entries[accepted["draft_entry_id"]]
    assert draft["status"] == "quarantined"
    assert draft["review_status"] == "pending"
    assert draft["retrieval_enabled"] is False
    # Adding a draft does not alter current authority.  The supersession edge
    # is created only by the later atomic staff approval.
    assert [item["id"] for item in main.resolve_knowledge_authority(list(entries.values()), "primary", NOW)] == ["price"]

    approved = main.approve_learned_information_entry(draft["id"])
    saved = {item["id"]: item for item in main.list_learned_information()}
    assert approved["status"] == "active"
    assert approved["review_status"] == "approved"
    assert approved["retrieval_enabled"] is True
    assert saved["price"]["status"] == "superseded"
    assert saved["price"]["retrieval_enabled"] is False
    assert saved[draft["id"]]["supersedes_id"] == "price"
    assert main.get_knowledge_curator_state()["proposals"][0]["status"] == "applied"
    assert knowledge.joinpath(main.LEARNED_INFORMATION_FILENAME).exists()


def test_stale_proposal_fails_closed_before_draft_creation(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    main.replace_learned_information_entry("price", {"text": "Changed after audit."})
    with pytest.raises(ValueError, match="fresh audit"):
        main.accept_knowledge_curator_proposal(proposal["id"])


def test_final_approval_rejects_a_stale_accepted_proposal(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    accepted = main.accept_knowledge_curator_proposal(proposal["id"])
    main.replace_learned_information_entry("price", {"text": "Changed after the draft was accepted."})
    with pytest.raises(ValueError, match="fresh audit"):
        main.accept_knowledge_curator_proposal(proposal["id"])
    with pytest.raises(ValueError, match="stale"):
        main.approve_learned_information_entry(accepted["draft_entry_id"])


def test_reject_and_dismiss_change_proposal_state_only(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("a", text="This costs $100."), record("b", text="This costs $100.")])
    proposals = main.run_knowledge_curator()["proposals"]
    original = main.list_learned_information()
    main.transition_knowledge_curator_proposal(proposals[0]["id"], "rejected")
    main.transition_knowledge_curator_proposal(proposals[1]["id"], "dismissed")
    assert main.list_learned_information() == original
    assert main.run_knowledge_curator()["run"]["created_proposals"] == 0


def test_curator_groups_are_account_isolated():
    records = [
        record("primary", text="Primary wording."),
        record("secondary", text="Secondary wording.", scope="secondary", sms_account_key="secondary"),
    ]
    findings = main.inspect_knowledge_integrity(records, now=NOW)
    assert "incompatible_active_records" not in {item["finding_type"] for item in findings}


def test_contextual_booking_pairs_are_not_competing_rules():
    records = [
        record("one", key="booking", text="I can help you find a time.", source_type="sms_pair_template", applies_when="When a customer asks for a Tuesday booking."),
        record("two", key="booking", text="Please use the booking link.", source_type="staff-edited-reply", applies_when="When a customer asks to move a booking."),
    ]
    assert "incompatible_active_records" not in finding_types(records)


def test_authoritative_rules_with_same_applicability_are_detected():
    records = [
        record("one", text="Use option A.", applies_when="When a customer asks about cancellation."),
        record("two", text="Use option B.", applies_when="When a customer asks about cancellation."),
    ]
    finding = next(item for item in main.inspect_knowledge_integrity(records, now=NOW) if item["finding_type"] == "incompatible_active_records")
    assert finding["evidence"]["same_applicability"] is True


def test_missing_applicability_is_precise_not_a_contradiction():
    findings = main.inspect_knowledge_integrity([record("one", text="A", applies_when=""), record("two", text="B", applies_when="")], now=NOW)
    assert "incompatible_active_records" not in {item["finding_type"] for item in findings}
    assert any(item["evidence"].get("missing_fields") == ["applies_when"] for item in findings)


@pytest.mark.parametrize("text", [
    "Check live availability before replying.",
    "When a customer asks about availability, use the calendar.",
    "Use the calendar to confirm available times.",
    "The next appointment is {date} at {time}.",
])
def test_general_availability_language_is_not_a_dynamic_claim(text):
    assert not main._curator_dynamic_claim_kind(text)


@pytest.mark.parametrize("text,kind", [
    ("The service costs $100.", "price"),
    ("The service takes 45 minutes.", "duration"),
    ("There is an available appointment at 3pm.", "availability"),
    ("Your booking is Tuesday at 3pm.", "booking_time"),
])
def test_concrete_dynamic_claims_are_detected(text, kind):
    assert main._curator_dynamic_claim_kind(text) == kind


def test_authenticated_response_has_previews_but_state_and_llm_do_not(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [record("sensitive", text="Sensitive reply costs $100.")])
    main.run_knowledge_curator()
    response = main.get_knowledge_curator_state()
    assert response["proposals"][0]["record_previews"][0]["knowledge_text"] == "Sensitive reply costs $100."
    saved = (data / "curator.json").read_text(encoding="utf-8")
    assert "Sensitive reply" not in saved and "record_previews" not in saved


def test_preview_fails_closed_for_stale_revision_and_every_action_is_rejected(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    main.replace_learned_information_entry("price", {"text": "Revision two private content costs $120."})
    preview = next(item for item in main.get_knowledge_curator_state()["proposals"] if item["id"] == proposal["id"])
    assert preview["actionable"] is False
    assert preview["record_previews"] == [{"reference_status": "stale", "id": "price", "expected_revision": 1, "current_revision": 2, "revision": 1}]
    assert "Revision two private content" not in json.dumps(preview)
    original = main.list_learned_information()
    for operation in (
        lambda: main.resolve_knowledge_curator_proposal(proposal["id"], "add_safe_replacement_draft"),
        lambda: main.accept_knowledge_curator_proposal(proposal["id"]),
        lambda: main.transition_knowledge_curator_proposal(proposal["id"], "dismissed"),
    ):
        with pytest.raises(ValueError, match="changed|unavailable"):
            operation()
    assert main.list_learned_information() == original


def test_preview_explicitly_marks_missing_reference_and_valid_references_still_render(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    valid = next(item for item in main.get_knowledge_curator_state()["proposals"] if item["id"] == proposal["id"])
    assert valid["actionable"] is True
    assert valid["record_previews"][0]["knowledge_text"] == "The service costs $100."
    main.delete_learned_information_entry("price")
    missing = next(item for item in main.get_knowledge_curator_state()["proposals"] if item["id"] == proposal["id"])
    assert missing["actionable"] is False
    assert missing["record_previews"] == [{"reference_status": "missing", "id": "price", "expected_revision": 1, "current_revision": None, "revision": 1}]
    with pytest.raises(ValueError, match="changed|unavailable"):
        main.resolve_knowledge_curator_proposal(proposal["id"], "add_safe_replacement_draft")


def test_authoritative_source_types_collide_by_role_not_raw_source_type():
    records = [
        record("manual", text="Use policy A.", source_type="manual_guidance", applies_when="When a customer asks about cancellations."),
        record("resolution", text="Use policy B.", source_type="information_request_resolution", applies_when="When a customer asks about cancellations."),
    ]
    assert "incompatible_active_records" in finding_types(records)


def test_authority_collision_identity_keeps_context_scope_and_conditions_separate():
    contextual = [
        record("pair-a", key="booking", text="Example A", source_type="sms_pair_template", applies_when="When the customer asks for Tuesday."),
        record("pair-b", key="booking", text="Example B", source_type="staff-edited-reply", applies_when="When the customer asks for Friday."),
    ]
    assert "incompatible_active_records" not in finding_types(contextual)
    distinct_conditions = [
        record("one", text="A", applies_when="When a customer cancels."),
        record("two", text="B", applies_when="When a customer reschedules."),
    ]
    assert "incompatible_active_records" not in finding_types(distinct_conditions)
    distinct_scopes = [
        record("primary", text="A", applies_when="When a customer cancels."),
        record("secondary", text="B", scope="secondary", sms_account_key="secondary", applies_when="When a customer cancels."),
    ]
    assert "incompatible_active_records" not in finding_types(distinct_scopes)


def test_version_one_state_migrates_safely_and_reconciles_without_touching_knowledge(tmp_path, monkeypatch):
    knowledge, data = curator_paths(tmp_path, monkeypatch, [record("context", key="booking", text="Contextual example.", source_type="sms_pair_template")])
    legacy = {
        "version": 1,
        "runs": [{"id": "legacy-run", "completed_at": "2030-01-01T00:00:00Z", "message": "history"}],
        "proposals": [{
            "id": "legacy-proposal", "fingerprint": "legacy-fingerprint", "canonical_key": "booking", "scope": "primary",
            "finding_type": "incompatible_active_records", "records": [{"id": "context", "revision": 1}],
            "reason_codes": ["curator_incompatible_active"], "evidence": {"reason_codes": ["curator_incompatible_active"], "record_count": 1},
            "proposed_action": "ask_owner", "confidence": "deterministic", "owner_questions": [], "status": "proposed",
            "created_at": "2030-01-01T00:00:00Z", "updated_at": "2030-01-01T00:00:00Z",
            "record_previews": [{"knowledge_text": "Legacy private source content"}],
        }],
    }
    state_path = data / "curator.json"
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    before = knowledge.joinpath(main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8")
    loaded = main.get_knowledge_curator_state()
    assert loaded["runs"][0]["id"] == "legacy-run"
    assert loaded["proposals"][0]["actionable"] is True
    main.run_knowledge_curator()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    migrated = next(item for item in saved["proposals"] if item["id"] == "legacy-proposal")
    assert saved["version"] == 2 and migrated["status"] == "resolved_no_longer_detected"
    assert "record_previews" not in json.dumps(saved) and "Legacy private source content" not in json.dumps(saved)
    assert knowledge.joinpath(main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8") == before


def test_version_one_stale_and_not_an_issue_proposals_remain_safe(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [record("current", text="The service costs $100.")])
    state_path = data / "curator.json"
    stale = {
        "id": "stale-v1", "fingerprint": "not-current", "canonical_key": "price", "scope": "primary",
        "finding_type": "literal_dynamic_authority", "records": [{"id": "current", "revision": 1}],
        "reason_codes": ["curator_literal_price"], "evidence": {"reason_codes": ["curator_literal_price"], "record_count": 1},
        "proposed_action": "draft_supersession", "confidence": "deterministic", "owner_questions": [], "status": "resolved_not_an_issue",
        "resolution": "not_an_issue", "created_at": "2030-01-01T00:00:00Z", "updated_at": "2030-01-01T00:00:00Z",
    }
    missing = {**stale, "id": "missing-v1", "fingerprint": "missing-v1", "records": [{"id": "missing", "revision": 1}]}
    state_path.write_text(json.dumps({"version": 1, "runs": [], "proposals": [stale, missing]}), encoding="utf-8")
    main.replace_learned_information_entry("current", {"text": "Changed revision two."})
    proposals = {item["id"]: item for item in main.get_knowledge_curator_state()["proposals"]}
    assert proposals["stale-v1"]["actionable"] is False and proposals["stale-v1"]["record_previews"][0]["reference_status"] == "stale"
    assert proposals["missing-v1"]["actionable"] is False and proposals["missing-v1"]["record_previews"][0]["reference_status"] == "missing"


def test_version_one_not_an_issue_is_suppressed_when_its_current_fingerprint_matches(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    finding = next(item for item in main.inspect_knowledge_integrity() if item["finding_type"] == "literal_dynamic_authority")
    legacy = {
        **finding, "id": "v1-not-an-issue", "status": "resolved_not_an_issue", "resolution": "not_an_issue",
        "created_at": "2030-01-01T00:00:00Z", "updated_at": "2030-01-01T00:00:00Z", "last_seen_at": "2030-01-01T00:00:00Z",
    }
    (data / "curator.json").write_text(json.dumps({"version": 1, "runs": [], "proposals": [legacy]}), encoding="utf-8")
    assert main.run_knowledge_curator()["run"]["created_proposals"] == 0


def _mixed_reference_proposal(records):
    return {
        "id": "mixed-reference", "fingerprint": "mixed-reference-fingerprint", "canonical_key": "policy", "scope": "primary",
        "finding_type": "incompatible_active_records", "records": records,
        "reason_codes": ["curator_incompatible_active"], "evidence": {"reason_codes": ["curator_incompatible_active"], "record_count": len(records) if isinstance(records, list) else 0},
        "proposed_action": "ask_owner", "confidence": "deterministic", "owner_questions": [], "status": "proposed",
        "created_at": "2030-01-01T00:00:00Z", "updated_at": "2030-01-01T00:00:00Z",
    }


@pytest.mark.parametrize(
    ("bad_reference", "reason"),
    [
        ({"id": "", "revision": 1}, "missing_reference_id"),
        ({"id": "valid", "revision": 0}, "invalid_reference_revision"),
        ({"id": "valid", "revision": "1"}, "invalid_reference_revision"),
        ("not-a-reference", "reference_not_object"),
    ],
)
def test_mixed_malformed_references_survive_and_block_every_mutation(tmp_path, monkeypatch, bad_reference, reason):
    _, data = curator_paths(tmp_path, monkeypatch, [record("valid", text="Current authoritative text.")])
    main._save_curator_state({"runs": [], "proposals": [_mixed_reference_proposal([{"id": "valid", "revision": 1}, bad_reference])]})
    before_entries = main.list_learned_information()
    before_state = (data / "curator.json").read_text(encoding="utf-8")
    presented = main.get_knowledge_curator_state()["proposals"][0]
    assert presented["actionable"] is False
    assert len(presented["record_previews"]) == 2
    assert presented["record_previews"][0]["knowledge_text"] == "Current authoritative text."
    malformed = next(item for item in presented["record_previews"] if item["reference_status"] == "malformed")
    assert malformed["reason_code"] == reason and malformed["position"] == 1
    persisted = json.loads(before_state)["proposals"][0]
    assert persisted["malformed_reference_count"] == 1 and persisted["malformed_references"][0]["reason_code"] == reason
    attempts = [
        lambda: main.accept_knowledge_curator_proposal("mixed-reference"),
        lambda: main.transition_knowledge_curator_proposal("mixed-reference", "dismissed"),
        lambda: main.resolve_knowledge_curator_proposal("mixed-reference", "keep_all_examples"),
        lambda: main.resolve_knowledge_curator_proposal("mixed-reference", "select_current_rule", ["valid"]),
        lambda: main.resolve_knowledge_curator_proposal("mixed-reference", "create_merged_draft"),
        lambda: main.resolve_knowledge_curator_proposal("mixed-reference", "create_consolidation_draft"),
        lambda: main.resolve_knowledge_curator_proposal("mixed-reference", "create_metadata_repair_draft"),
    ]
    for attempt in attempts:
        with pytest.raises(ValueError, match="changed|unavailable"):
            attempt()
    assert main.list_learned_information() == before_entries
    assert (data / "curator.json").read_text(encoding="utf-8") == before_state


@pytest.mark.parametrize(
    ("records_value", "reason"),
    [
        ("not-a-list", "records_not_list"),
        ([], "empty_records"),
        ([{"id": "", "revision": 0}], "missing_reference_id"),
    ],
)
def test_nonlist_empty_and_only_malformed_references_remain_explicit_after_save_reload(tmp_path, monkeypatch, records_value, reason):
    _, data = curator_paths(tmp_path, monkeypatch, [record("valid", text="Current text.")])
    main._save_curator_state({"runs": [], "proposals": [_mixed_reference_proposal(records_value)]})
    saved = json.loads((data / "curator.json").read_text(encoding="utf-8"))["proposals"][0]
    assert saved["malformed_reference_count"] >= 1
    assert any(item["reason_code"] == reason for item in saved["malformed_references"])
    reloaded = main.get_knowledge_curator_state()["proposals"][0]
    assert reloaded["actionable"] is False
    assert any(item["reference_status"] == "malformed" and item.get("reason_code") == reason for item in reloaded["record_previews"])
    assert "Current text." not in json.dumps(reloaded["record_previews"])


def test_version_one_mixed_reference_state_migrates_without_losing_the_bad_reference(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [record("valid", text="Current text.")])
    legacy = _mixed_reference_proposal([{"id": "valid", "revision": 1}, {"id": "", "revision": "not-an-int", "source": "private"}])
    legacy["record_previews"] = [{"knowledge_text": "Never persist this preview"}]
    path = data / "curator.json"
    path.write_text(json.dumps({"version": 1, "runs": [], "proposals": [legacy]}), encoding="utf-8")
    presented = main.get_knowledge_curator_state()["proposals"][0]
    assert presented["actionable"] is False and len(presented["record_previews"]) == 2
    main._save_curator_state(main._load_curator_state())
    saved = path.read_text(encoding="utf-8")
    assert "Never persist this preview" not in saved
    migrated = json.loads(saved)["proposals"][0]
    assert migrated["malformed_reference_count"] == 1
    assert migrated["malformed_references"] == [{"position": 1, "reason_code": "missing_reference_id", "reference_status": "malformed"}]


def test_mixed_malformed_reference_api_routes_fail_closed(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("valid", text="Current text.")])
    main._save_curator_state({"runs": [], "proposals": [_mixed_reference_proposal([{"id": "valid", "revision": 1}, {"id": "", "revision": 1}])]})
    monkeypatch.setattr(main, "AUTH_PASSWORD", "curator-admin-password")
    client = TestClient(main.app)
    expires = int(datetime.now().timestamp()) + 300
    client.cookies.set(main.AUTH_COOKIE_NAME, main._admin_session_token(expires))
    assert client.post("/api/settings/knowledge-curator/proposals/mixed-reference/dismiss").status_code == 409
    assert client.post("/api/settings/knowledge-curator/proposals/mixed-reference/resolve", json={"resolution": "create_merged_draft"}).status_code == 409


def test_authenticated_transition_endpoint_fails_closed_for_stale_proposal(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    main.replace_learned_information_entry("price", {"text": "Changed revision two."})
    monkeypatch.setattr(main, "AUTH_PASSWORD", "curator-admin-password")
    client = TestClient(main.app)
    expires = int(datetime.now().timestamp()) + 300
    client.cookies.set(main.AUTH_COOKIE_NAME, main._admin_session_token(expires))
    response = client.post(f"/api/settings/knowledge-curator/proposals/{proposal['id']}/dismiss")
    assert response.status_code == 409


def test_resolution_controls_are_proposal_only_and_validate_selected_records(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [
        record("one", text="Use A.", applies_when="When cancellation is requested."),
        record("two", text="Use B.", applies_when="When cancellation is requested."),
    ])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "incompatible_active_records")
    original = main.list_learned_information()
    with pytest.raises(ValueError, match="referenced"):
        main.resolve_knowledge_curator_proposal(proposal["id"], "select_current_rule", ["not-in-proposal"])
    resolved = main.resolve_knowledge_curator_proposal(proposal["id"], "select_current_rule", ["one"])
    assert resolved["status"] == "resolved"
    assert main.list_learned_information() == original


def test_draft_resolutions_remain_quarantined_and_clean_reruns_close_obsolete_cards(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    draft = main.resolve_knowledge_curator_proposal(proposal["id"], "add_safe_replacement_draft")
    assert main.list_learned_information()[-1]["retrieval_enabled"] is False
    main.replace_learned_information_entry("price", {"text": "Check Settings for the current price."})
    main.run_knowledge_curator()
    state = json.loads((data / "curator.json").read_text(encoding="utf-8"))
    assert next(item for item in state["proposals"] if item["id"] == draft["id"])["status"] == "resolved_no_longer_detected"


def test_not_an_issue_stays_suppressed_until_material_change(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    proposal = next(item for item in main.run_knowledge_curator()["proposals"] if item["finding_type"] == "literal_dynamic_authority")
    main.resolve_knowledge_curator_proposal(proposal["id"], "not_an_issue")
    assert main.run_knowledge_curator()["run"]["created_proposals"] == 0
    main.replace_learned_information_entry("price", {"text": "The service costs $120."})
    main.replace_learned_information_entry("price", {"status": "active", "review_status": "approved", "retrieval_enabled": True})
    assert main.run_knowledge_curator()["run"]["created_proposals"] == 1


def test_curator_lock_rejects_concurrent_run(monkeypatch):
    assert main.KNOWLEDGE_CURATOR_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(main.HTTPException) as rejected:
            main.run_knowledge_curator()
        assert rejected.value.status_code == 409
    finally:
        main.KNOWLEDGE_CURATOR_LOCK.release()


def test_curator_settings_api_is_admin_protected(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [])
    monkeypatch.setattr(main, "AUTH_PASSWORD", "curator-admin-password")
    client = TestClient(main.app)
    assert client.get("/api/settings/knowledge-curator").status_code == 401
    expires = int(datetime.now().timestamp()) + 300
    client.cookies.set(main.AUTH_COOKIE_NAME, main._admin_session_token(expires))
    response = client.get("/api/settings/knowledge-curator")
    assert response.status_code == 200
    assert response.json() == {
        "runs": [], "proposals": [],
        "automation": {
            "enabled": False, "interval_seconds": 86400,
            "last_run_at": None, "last_status": None,
        },
    }


def test_curator_previews_are_returned_only_to_authenticated_settings_client(tmp_path, monkeypatch):
    curator_paths(tmp_path, monkeypatch, [record("preview", text="Private approved reply costs $100.")])
    main.run_knowledge_curator()
    monkeypatch.setattr(main, "AUTH_PASSWORD", "curator-admin-password")
    client = TestClient(main.app)
    assert client.get("/api/settings/knowledge-curator").status_code == 401
    expires = int(datetime.now().timestamp()) + 300
    client.cookies.set(main.AUTH_COOKIE_NAME, main._admin_session_token(expires))
    response = client.get("/api/settings/knowledge-curator")
    assert response.status_code == 200
    assert response.json()["proposals"][0]["record_previews"][0]["knowledge_text"] == "Private approved reply costs $100."


def test_curator_retention_is_bounded(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [])
    main._save_curator_state({
        "runs": [{"id": f"r-{index}"} for index in range(main.KNOWLEDGE_CURATOR_MAX_RUNS + 5)],
        "proposals": [{"id": f"p-{index}"} for index in range(main.KNOWLEDGE_CURATOR_MAX_PROPOSALS + 5)],
    })
    saved = json.loads((data / "curator.json").read_text(encoding="utf-8"))
    assert len(saved["runs"]) == main.KNOWLEDGE_CURATOR_MAX_RUNS
    assert len(saved["proposals"]) == main.KNOWLEDGE_CURATOR_MAX_PROPOSALS


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("insufficient_quota", True),
        ("billing_hard_limit_reached", True),
        ("rate_limit_exceeded", False),
        ("invalid_api_key", False),
        ("timeout", False),
        ("", False),
    ],
)
def test_quota_classifier_is_narrow_and_structured(code, expected):
    error = SimpleNamespace(code=code, body={"error": {"code": code}})
    assert main.is_openai_quota_exhausted(error) is expected


def test_quota_classifier_prefers_nested_provider_code_over_http_status():
    error = SimpleNamespace(code="429", body={"error": {"code": "insufficient_quota"}})
    assert main.is_openai_quota_exhausted(error) is True


def test_legacy_metadata_maintenance_is_manual_preview_only(tmp_path, monkeypatch):
    legacy = record("legacy", text="Keep this exact customer wording.")
    for field in ("revision", "version", "status", "canonical_key", "created_at"):
        legacy.pop(field)
    legacy["updated_at"] = "2030-01-02T00:00:00Z"
    knowledge, data = curator_paths(tmp_path, monkeypatch, [legacy])
    before_text = (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8")

    first = main.run_knowledge_curator()
    assert (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8") == before_text
    assert first["run"]["safe_repairs_completed"] == 0
    assert "invalid_metadata" in first["run"]["finding_counts"]
    state = json.loads((data / "curator.json").read_text(encoding="utf-8"))
    audit = state["maintenance_history"][-1]
    assert audit["result"] == "proposal_only" and audit["repairs"] == []
    assert "Keep this exact" not in json.dumps(audit)
    second = main.run_knowledge_curator()
    assert second["run"]["safe_repairs_completed"] == 0


def test_ambiguous_or_invalid_legacy_metadata_is_not_repaired(tmp_path, monkeypatch):
    ambiguous = record("ambiguous", status="not-a-status", revision=0)
    ambiguous.pop("canonical_key")
    other = record("other", key="use-the-approved-wording", text="Different authority.")
    knowledge, _ = curator_paths(tmp_path, monkeypatch, [ambiguous, other])
    before = (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8")
    result = main.run_knowledge_curator()
    assert result["run"]["safe_repairs_completed"] == 0
    assert (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8") == before
    assert "invalid_metadata" in result["run"]["finding_counts"]


def test_configured_automatic_curator_is_due_once_and_remains_proposal_only(tmp_path, monkeypatch):
    knowledge, data = curator_paths(tmp_path, monkeypatch, [
        record("primary-price", text="The service costs $100."),
        record("secondary-price", text="The service costs $200.", scope="secondary", sms_account_key="secondary"),
    ])
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_ENABLED", True)
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", True)
    before = (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8")

    first = main.run_due_knowledge_curator(now=NOW)
    second = main.run_due_knowledge_curator(now=NOW)

    assert first["status"] == "completed"
    assert first["run"]["trigger"] == "automatic"
    assert first["run"]["created_proposals"] >= 2
    assert second == {"status": "not_due"}
    assert (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8") == before
    saved = json.loads((data / "curator.json").read_text(encoding="utf-8"))
    assert saved["runs"][-1]["trigger"] == "automatic"
    assert saved["maintenance_history"][-1]["trigger"] == "automatic"
    assert all(item["status"] == "proposed" for item in saved["proposals"])
    incompatible = [item for item in saved["proposals"] if item["finding_type"] == "incompatible_active_records"]
    assert incompatible == []
    automation = main.get_knowledge_curator_state()["automation"]
    assert automation == {
        "enabled": True, "interval_seconds": 3600,
        "last_run_at": "2030-01-15T12:00:00Z", "last_status": "completed_with_warning",
    }


def test_automatic_curator_is_disabled_and_invalid_configuration_fails_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "inspect_knowledge_integrity", lambda: calls.append(True))
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_ENABLED", False)
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_INTERVAL_SECONDS", 3600)
    assert main.run_due_knowledge_curator(now=NOW) == {"status": "disabled"}
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_ENABLED", True)
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_INTERVAL_SECONDS", None)
    assert main.run_due_knowledge_curator(now=NOW) == {"status": "disabled"}
    assert calls == []
    assert main._knowledge_curator_auto_interval("not-a-number") is None
    assert main._knowledge_curator_auto_interval(299) is None


def test_automatic_curator_failure_is_audited_and_not_retried_early(tmp_path, monkeypatch):
    knowledge, data = curator_paths(tmp_path, monkeypatch, [record("safe")])
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_ENABLED", True)
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(main, "inspect_knowledge_integrity", lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))
    before = (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8")

    failed = main.run_due_knowledge_curator(now=NOW)
    retry = main.run_due_knowledge_curator(now=NOW)

    assert failed["status"] == "failed"
    assert failed["run"]["error_code"] == "curator_automatic_run_failed"
    assert retry == {"status": "not_due"}
    assert (knowledge / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8") == before
    saved = json.loads((data / "curator.json").read_text(encoding="utf-8"))
    assert saved["runs"][-1]["status"] == "failed"
    assert "synthetic" not in json.dumps(saved)


def test_automatic_curator_does_not_overwrite_invalid_state(tmp_path, monkeypatch):
    _, data = curator_paths(tmp_path, monkeypatch, [record("safe")])
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_ENABLED", True)
    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_AUTO_INTERVAL_SECONDS", 3600)
    state_path = data / "curator.json"
    state_path.write_text("{invalid", encoding="utf-8")

    assert main.run_due_knowledge_curator(now=NOW) == {"status": "state_unavailable"}
    assert state_path.read_text(encoding="utf-8") == "{invalid"


def test_failed_metadata_bulk_repair_rolls_back_the_original_file(tmp_path, monkeypatch):
    legacy = record("legacy")
    legacy.pop("revision")
    legacy.pop("version")
    knowledge, _ = curator_paths(tmp_path, monkeypatch, [legacy])
    filepath = knowledge / main.LEARNED_INFORMATION_FILENAME
    before = filepath.read_text(encoding="utf-8")

    def fail_reload():
        raise RuntimeError("simulated reload failure")

    monkeypatch.setattr(main, "load_knowledge_base", fail_reload)
    repair = main._repair_legacy_knowledge_metadata()
    assert repair["result"] == "rolled_back"
    assert repair["repairs"] == []
    assert filepath.read_text(encoding="utf-8") == before
    assert list((knowledge / f"{main.LEARNED_INFORMATION_FILENAME}.curator-backups").glob("*.jsonl"))


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("invalid_api_key", "curator_model_authentication_failed"),
        ("model_not_found", "curator_model_inaccessible"),
        ("rate_limit_exceeded", "curator_model_rate_limited"),
        ("timeout", "curator_model_timeout"),
        ("server_error", "curator_model_provider_error"),
    ],
)
def test_model_failures_are_safely_and_structurally_classified(code, expected):
    error = SimpleNamespace(code=code, body={"error": {"code": code}})
    assert main._classify_curator_model_failure(error) == expected
    assert "provider" not in main._curator_owner_model_message(expected).casefold()


def test_curator_uses_the_configured_supported_model_without_retrying(monkeypatch):
    calls = []

    class CapturingResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text='{"proposals": []}')

    monkeypatch.setattr(main, "KNOWLEDGE_CURATOR_MODEL", "configured-supported-model")
    monkeypatch.setattr(main, "openai_client", SimpleNamespace(responses=CapturingResponses()))
    main._curator_enrich_proposals(main.inspect_knowledge_integrity([record("price", text="Costs $100.")], now=NOW))
    assert len(calls) == 1 and calls[0]["model"] == "configured-supported-model" and calls[0]["store"] is False

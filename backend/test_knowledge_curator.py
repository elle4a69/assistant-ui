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
    curator_paths(tmp_path, monkeypatch, [record("price", text="The service costs $100.")])
    first = main.run_knowledge_curator()
    second = main.run_knowledge_curator()
    assert first["run"]["created_proposals"] >= 1
    assert second["run"]["created_proposals"] == 0
    state = main.get_knowledge_curator_state()
    unresolved = [item for item in state["proposals"] if item["status"] == "proposed"]
    assert len({item["fingerprint"] for item in unresolved}) == len(unresolved)
    serialized = json.dumps(state)
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
    assert response.json() == {"runs": [], "proposals": []}


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

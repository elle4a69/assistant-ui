from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import main
from services import business_assistant_service as business


def test_business_assistant_hard_excludes_coding_and_deployment_tools():
    names = business.BUSINESS_ASSISTANT_TOOL_NAMES
    assert "start_coding_task" not in names
    assert "read_code_file" not in names
    assert "inspect_code_changes" not in names
    assert "propose_code_deployment" not in names
    assert "execute_code_deployment" not in names
    assert "propose_runtime_change" not in names
    assert "execute_runtime_change" not in names
    assert "draft_business_rule" in names
    assert "confirm_business_rule" in names
    assert "list_curator_questions" in names
    assert "create_maintenance_handoff" in names


def test_business_assistant_instructions_define_user_level_boundary():
    text = business.business_assistant_instructions("{}", "[]", "")
    assert "absolutely no coding" in text
    assert "onboarding" in text
    assert "ask one clear question at a time" in text
    assert "explicit confirmation" in text
    assert "create_maintenance_handoff" in text


def test_business_assistant_rejects_hidden_engineering_tool(monkeypatch):
    db = SimpleNamespace()
    result = business.execute_business_assistant_tool(
        db,
        "start_coding_task",
        {"title": "x"},
        "do it",
    )
    assert result["status"] == "rejected"
    assert "maintenance/engineering" in result["reason"]


def test_confirmation_requires_affirmative_quote(monkeypatch):
    monkeypatch.setattr(business, "list_learned_information", lambda: [{
        "id": "manual-1",
        "source_type": "manual_guidance",
    }])
    called = []
    monkeypatch.setattr(
        business,
        "approve_learned_information_entry",
        lambda entry_id: called.append(entry_id) or {
            "retrieval_enabled": True,
            "scope": "primary",
            "review_status": "approved",
        },
    )
    rejected = business.execute_business_assistant_tool(
        SimpleNamespace(),
        "confirm_business_rule",
        {"entry_id": "manual-1", "confirmation_quote": "maybe"},
        "",
    )
    assert rejected["status"] == "rejected"
    assert called == []

    confirmed = business.execute_business_assistant_tool(
        SimpleNamespace(),
        "confirm_business_rule",
        {"entry_id": "manual-1", "confirmation_quote": "yes"},
        "",
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["active_for_replies"] is True
    assert called == ["manual-1"]


def test_private_internal_record_does_not_become_owner_question():
    record = {
        "id": "private-1",
        "type": "manual_guidance",
        "source_type": "manual_guidance",
        "canonical_key": "internal-note",
        "scope": "internal",
        "sms_account_key": "internal",
        "topic": "Internal note",
        "text": "Internal operational note.",
        "created_at": "2030-01-01T00:00:00Z",
        "updated_at": "2030-01-01T00:00:00Z",
        "status": "quarantined",
        "revision": 1,
        "review_status": "pending",
        "retrieval_enabled": False,
        "category": "internal_or_uncertain",
    }
    findings = main.inspect_knowledge_integrity([record])
    assert not any(item["finding_type"] == "owner_answer_required" for item in findings)

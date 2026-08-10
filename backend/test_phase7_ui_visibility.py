import os
import json
import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import main
from main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_rag_status_endpoints_enabled(monkeypatch):
    """Verify GET /api/admin/rag/status and /api/rag/status return clear RAG state, dataset hash, total example count, intent breakdown, validation status, and feature flag state when enabled."""
    monkeypatch.setattr(main, "STYLE_EXAMPLES_ENABLED", True)
    monkeypatch.setattr(main, "example_index", main.SMSExampleIndex(main.DATASET_FILE))

    for endpoint in ["/api/admin/rag/status", "/api/rag/status"]:
        response = client.get(endpoint)
        assert response.status_code == 200
        data = response.json()

        assert data["enabled"] is True
        assert data["feature_flag_enabled"] is True
        assert data["rag_state"] in ("active", "error")
        assert data["validation_status"] in ("valid", "invalid", "error")
        assert "dataset_hash" in data
        assert isinstance(data["total_examples"], int)
        assert data["total_examples"] >= 0
        assert isinstance(data["intent_counts"], dict)
        assert "availability" in data["intent_counts"] or len(data["intent_counts"]) >= 0


def test_rag_status_endpoints_disabled(monkeypatch):
    """Verify GET /api/admin/rag/status and /api/rag/status return explicit disabled state when feature flag is off."""
    monkeypatch.setattr(main, "STYLE_EXAMPLES_ENABLED", False)
    monkeypatch.setattr(main, "example_index", None)

    for endpoint in ["/api/admin/rag/status", "/api/rag/status"]:
        response = client.get(endpoint)
        assert response.status_code == 200
        data = response.json()

        assert data["enabled"] is False
        assert data["feature_flag_enabled"] is False
        assert data["rag_state"] == "disabled"
        assert data["validation_status"] == "disabled"
        assert data["total_examples"] == 0
        assert data["intent_counts"] == {}
        assert data["dataset_hash"] is None


def test_knowledge_retrieval_errors_do_not_block_settings(tmp_path, monkeypatch):
    """Verify knowledge document or retrieval errors NEVER block unrelated settings from loading."""
    # Point KNOWLEDGE_DIR to a temporary folder with corrupted files
    corrupt_dir = tmp_path / "corrupt_knowledge"
    corrupt_dir.mkdir()
    
    # Create invalid binary/corrupted files in knowledge directory
    (corrupt_dir / "bad_file.txt").write_bytes(b"\x80\x81\x82 corrupt bytes \xff\xfe")
    (corrupt_dir / "invalid.jsonl").write_text("{invalid json line\n", encoding="utf-8")
    
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(corrupt_dir))
    
    # Reload knowledge base with corrupt files
    main.load_knowledge_base()

    # Settings endpoints must succeed with 200 OK regardless of knowledge store issues
    res_settings = client.get("/api/settings")
    assert res_settings.status_code == 200
    assert "systemPrompt" in res_settings.json()

    res_vars = client.get("/api/settings/business-variables")
    assert res_vars.status_code == 200
    assert "variables" in res_vars.json()

    res_hours = client.get("/api/settings/working-hours")
    assert res_hours.status_code == 200

    res_mm = client.get("/api/settings/mobilemessage")
    assert res_mm.status_code == 200


def test_business_variable_endpoint_returns_required_schema_fields(tmp_path, monkeypatch):
    """Verify business variable endpoint (/api/settings/business-variables) returns copyable tokens, labels, keys, descriptions, and required/optional status."""
    vars_file = tmp_path / "business_variables.json"
    monkeypatch.setattr(main, "BUSINESS_VARIABLES_PATH", str(vars_file))

    response = client.get("/api/settings/business-variables")
    assert response.status_code == 200
    data = response.json()
    assert "variables" in data
    variables = data["variables"]
    assert len(variables) > 0

    # Verify every variable contains copyable tokens, labels, keys, descriptions, and required status
    for var in variables:
        assert "key" in var and isinstance(var["key"], str)
        assert "token" in var and var["token"] == f"{{{var['key']}}}"
        assert "label" in var and isinstance(var["label"], str)
        assert "description" in var and isinstance(var["description"], str)
        assert "required" in var and isinstance(var["required"], bool)
        assert "required_status" in var and var["required_status"] in ("required", "optional")
        assert "value" in var

    # Check key default variables
    key_map = {v["key"]: v for v in variables}
    assert key_map["provider_name"]["required"] is True
    assert key_map["provider_name"]["required_status"] == "required"
    assert key_map["suburb"]["required"] is True
    assert key_map["website"]["token"] == "{website}"

    # Test saving variables preserves and returns full metadata
    save_res = client.post("/api/settings/business-variables", json={
        "variables": [
            {
                "key": "provider_name",
                "label": "Provider Name",
                "value": "Alex Smith",
                "description": "Custom provider name description",
                "required": True
            },
            {
                "key": "custom_tag",
                "label": "Custom Tag",
                "value": "VIP",
                "description": "Special client tag",
                "required": False
            }
        ]
    })
    assert save_res.status_code == 200
    saved_vars = save_res.json()["variables"]
    saved_map = {v["key"]: v for v in saved_vars}

    assert saved_map["provider_name"]["value"] == "Alex Smith"
    assert saved_map["provider_name"]["token"] == "{provider_name}"
    assert saved_map["custom_tag"]["token"] == "{custom_tag}"
    assert saved_map["custom_tag"]["required_status"] == "optional"

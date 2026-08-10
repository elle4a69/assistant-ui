import json

import pytest
from fastapi import HTTPException

import main


def test_business_variables_persist_and_are_available_without_restart(tmp_path, monkeypatch):
    variables_path = tmp_path / "business_variables.json"
    monkeypatch.setattr(main, "BUSINESS_VARIABLES_PATH", str(variables_path))

    result = main.save_business_variables(main.BusinessVariablesInput(variables=[
        main.BusinessVariableInput(
            key="provider_name",
            label="Provider name",
            value="Taylor",
        ),
        main.BusinessVariableInput(
            key="street_address",
            label="Street address",
            value="10 Example Street",
        ),
    ]))

    assert result["status"] == "success"
    assert json.loads(variables_path.read_text(encoding="utf-8"))[0]["value"] == "Taylor"
    assert main.get_business_variable_values() == {
        "provider_name": "Taylor",
        "street_address": "10 Example Street",
    }
    assert "Provider name: Taylor" in main.get_live_business_variables_context()


def test_business_variable_renderer_supports_prompts_and_confirmations():
    rendered = main.render_template_variables(
        "Hi {name}, see {provider_name} at {street_address}. Keep {unknown} visible.",
        {
            "name": "Alex",
            "provider_name": "Taylor",
            "street_address": "10 Example Street",
        },
    )

    assert rendered == (
        "Hi Alex, see Taylor at 10 Example Street. Keep {unknown} visible."
    )


def test_customer_and_prompt_tokens_cannot_be_redefined(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "BUSINESS_VARIABLES_PATH", str(tmp_path / "variables.json"))

    with pytest.raises(HTTPException) as exc_info:
        main.save_business_variables(main.BusinessVariablesInput(variables=[
            main.BusinessVariableInput(key="name", label="Name", value="Wrong name")
        ]))

    assert exc_info.value.status_code == 422
    assert "reserved" in exc_info.value.detail


def test_blank_values_are_not_added_to_ai_context(tmp_path, monkeypatch):
    variables_path = tmp_path / "business_variables.json"
    variables_path.write_text(json.dumps([
        {"key": "suburb", "label": "Suburb", "value": ""},
        {"key": "website", "label": "Website", "value": "https://example.test"},
    ]), encoding="utf-8")
    monkeypatch.setattr(main, "BUSINESS_VARIABLES_PATH", str(variables_path))

    context = main.get_live_business_variables_context()

    assert "Suburb" not in context
    assert "Website: https://example.test" in context


def test_curation_variables_are_available_in_defaults():
    default_keys = {item["key"] for item in main.BUSINESS_VARIABLE_DEFAULTS}

    assert "booking_url" in default_keys

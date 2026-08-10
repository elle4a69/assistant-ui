import os
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

import main


def test_website_canonical_key_standardization(tmp_path, monkeypatch):
    """Verify provider website is standardized as canonical key {website}."""
    variables_path = tmp_path / "business_variables.json"
    monkeypatch.setattr(main, "BUSINESS_VARIABLES_PATH", str(variables_path))

    # Save setting using booking_url alias
    main.save_business_variables(main.BusinessVariablesInput(variables=[
        main.BusinessVariableInput(key="booking_url", label="Booking URL", value="https://example.com/book"),
        main.BusinessVariableInput(key="provider_name", label="Provider Name", value="Taylor"),
    ]))

    values = main.get_business_variable_values()
    assert values.get("website") == "https://example.com/book"
    assert values.get("booking_url") == "https://example.com/book"
    assert values.get("provider_name") == "Taylor"

    rendered = main.render_template_variables("Visit {website} or {booking_url}", values)
    assert rendered == "Visit https://example.com/book or https://example.com/book"


def test_strict_allowlist_validation_checkpoint():
    """Verify strict allowlist validation passes rendered text and rejects unrendered target tokens or unmapped patterns."""
    # Fully rendered text (business variables substituted)
    rendered_text = "Hi Alex, see Taylor at https://example.com. Call +61412345678."
    main.validate_no_unresolved_placeholders(rendered_text, context_label="test")

    # Rejects unmapped patterns
    with pytest.raises(ValueError) as exc1:
        main.validate_no_unresolved_placeholders("<UNMAPPED_URL>", context_label="test")
    assert "Unresolved pattern" in str(exc1.value)

    # Rejects unrendered critical tokens
    with pytest.raises(ValueError) as exc2:
        main.validate_no_unresolved_placeholders("Visit {website} now", context_label="test")
    assert "Unrendered token" in str(exc2.value)

    with pytest.raises(ValueError) as exc3:
        main.validate_no_unresolved_placeholders("Hi from {provider_name}", context_label="test")
    assert "Unrendered token" in str(exc3.value)

    with pytest.raises(ValueError) as exc4:
        main.validate_no_unresolved_placeholders("Located in {suburb}", context_label="test")
    assert "Unrendered token" in str(exc4.value)

    # Rejects placeholders outside strict allowlist
    with pytest.raises(ValueError) as exc5:
        main.validate_no_unresolved_placeholders("Your {secret_code} is 123", context_label="test")
    assert "Unallowed placeholder" in str(exc5.value)


def test_startup_validation_in_style_index(tmp_path):
    """Verify SMSExampleIndex startup validation fails on unmapped tokens or invalid placeholders."""
    # Invalid dataset record with unmapped token
    bad_corpus = tmp_path / "bad_examples.jsonl"
    bad_corpus.write_text(
        json.dumps({
            "id": "bad1",
            "intent": "availability",
            "review_status": "approved",
            "messages": [
                {"role": "user", "content": "Are you free?"},
                {"role": "assistant", "content": "Yes visit <UNMAPPED_URL>"}
            ]
        }) + "\n",
        encoding="utf-8"
    )

    with pytest.raises(ValueError) as exc:
        main.SMSExampleIndex(bad_corpus)
    assert "Unresolved placeholder" in str(exc.value)


def test_rendered_style_examples_removes_unrendered_tokens():
    """Verify render_style_examples substitutes variables and strips unrendered placeholder tokens."""
    examples = [
        ("Are you around {suburb}?", "Yes, check {website} or call {phone}.")
    ]
    biz_vars = {
        "website": "https://example.com",
        "phone": "+61412345678",
    }
    # suburb is intentionally left missing/empty
    rendered = main.render_style_examples(examples, biz_vars)

    assert len(rendered) == 1
    inc, rep = rendered[0]
    assert "{suburb}" not in inc
    assert "https://example.com" in rep
    assert "+61412345678" in rep
    assert "{website}" not in rep


def test_assemble_safe_prompt_pipeline(tmp_path, monkeypatch):
    """Verify full 8-step prompt assembly pipeline order."""
    variables_path = tmp_path / "business_variables.json"
    monkeypatch.setattr(main, "BUSINESS_VARIABLES_PATH", str(variables_path))

    main.save_business_variables(main.BusinessVariablesInput(variables=[
        main.BusinessVariableInput(key="website", label="Website", value="https://example.com"),
        main.BusinessVariableInput(key="provider_name", label="Provider Name", value="Taylor"),
        main.BusinessVariableInput(key="suburb", label="Suburb", value="South Yarra"),
    ]))

    now = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
    system_tmpl = "You are {provider_name}'s assistant in {suburb}."
    user_tmpl = "Customer: {message}\nWebsite: {website}\nKnowledge: {knowledge}\nSlots: {slots}"

    instructions, user_prompt, examples = main.assemble_safe_prompt(
        system_tmpl,
        user_tmpl,
        query="Are you free today?",
        retrieved_context="No extra info",
        slots_str="Option 1: 2pm",
        now_local=now,
    )

    assert "Taylor" in instructions
    assert "South Yarra" in instructions
    assert "{provider_name}" not in instructions
    assert "{suburb}" not in instructions
    assert "https://example.com" in user_prompt
    assert "{website}" not in user_prompt

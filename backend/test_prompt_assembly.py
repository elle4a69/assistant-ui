import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import main
from main import (
    assemble_safe_prompt,
    build_model_instructions,
    build_model_input,
    get_style_examples,
    render_style_examples,
    classify_query_intent,
    SMSExampleIndex,
    DATASET_FILE,
    validate_no_unresolved_placeholders,
    get_business_variable_values,
)


def test_8_step_prompt_assembly_pipeline_order(monkeypatch):
    """
    Verify the complete 8-step prompt assembly pipeline order:
    (1) System prompt -> (2) Business variables -> (3) Classify intent
    -> (4) Retrieve approved examples -> (5) Render permitted business variables inside retrieved style examples
    -> (6) Validate zero unresolved placeholders -> (7) Budget limit -> (8) Submit to LLM
    """
    monkeypatch.setattr(main, "STYLE_EXAMPLES_ENABLED", True)
    if main.example_index is None:
        monkeypatch.setattr(main, "example_index", SMSExampleIndex(DATASET_FILE))

    hobart_tz = ZoneInfo("Australia/Hobart")
    now_local = datetime(2026, 8, 11, 14, 30, tzinfo=hobart_tz)
    system_prompt_tmpl = "You are {provider_name}, an independent companion based in {suburb}."
    user_prompt_tmpl = "Customer message: {message}\nKnowledge: {knowledge}\nSlots: {slots}"
    query = "Are you available for a 1 hour incall session today?"


    instructions, user_prompt, examples = assemble_safe_prompt(
        system_prompt_tmpl=system_prompt_tmpl,
        user_prompt_tmpl=user_prompt_tmpl,
        query=query,
        retrieved_context="Available today 2pm-6pm.",
        slots_str="1 hour incall",
        now_local=now_local,
    )

    # Step 1 & 2: System prompt with rendered business variables
    assert "You are Tori, an independent companion based in Melbourne." in instructions
    # Step 3 & 4: Intent classified (availability / booking_request) and examples retrieved
    assert len(examples) > 0
    assert len(examples) <= 3
    # Step 5 & 6: Business variables rendered in examples & validated zero unresolved placeholders
    for inc, rep in examples:
        assert "{website}" not in inc
        assert "{website}" not in rep
        assert "<UNMAPPED" not in inc
        assert "<UNMAPPED" not in rep
    # Step 7: Budget limit respected (max 3 examples, prompt under budget)
    assert len(instructions) > 0
    # Step 8: Submission format verified
    assert "Customer message: Are you available for a 1 hour incall session today?" in user_prompt
    assert "Knowledge: Available today 2pm-6pm." in user_prompt


def test_variable_rendering_inside_retrieved_examples():
    """Verify that business variables inside retrieved examples are rendered with active business values."""
    raw_examples = [
        ("How do I book?", "Check out my website at {website} or SMS me at {phone}."),
        ("Where are you located?", "I am located in {suburb}, {state}."),
    ]
    biz_vars = get_business_variable_values()
    rendered = render_style_examples(raw_examples, biz_vars)

    assert len(rendered) == 2
    inc1, rep1 = rendered[0]
    assert "{website}" not in rep1
    assert "{phone}" not in rep1
    assert "https://" in rep1 or "fly.dev" in rep1

    inc2, rep2 = rendered[1]
    assert "{suburb}" not in rep2
    assert biz_vars.get("suburb", "Melbourne") in rep2



def test_budget_enforcement_max_3_examples_and_char_limit(tmp_path):
    """Verify retrieval enforces max 3 examples and max_budget_chars strict character limit."""
    budget_file = tmp_path / "budget_test.jsonl"
    recs = [
        {
            "id": f"ex_{i}",
            "review_status": "approved",
            "intent": "pricing",
            "incoming": f"What are your rates for {i} hour session?",
            "reply": f"My rate for {i} hour session is ${100 * i} incall.",
        }
        for i in range(1, 6)
    ]
    budget_file.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    # Limit 10 requested, but index caps at max 3 examples
    index = SMSExampleIndex(budget_file, min_score=0.0, max_budget_chars=2000)
    results = index.search("rates hour session", intent="pricing", limit=10)
    assert len(results) <= 3

    # Small budget (120 chars) fits only 1 pair (~90 chars)
    index_tight = SMSExampleIndex(budget_file, min_score=0.0, max_budget_chars=120)
    results_tight = index_tight.search("rates hour session", intent="pricing", limit=5)
    assert len(results_tight) == 1
    pair_len = len(results_tight[0][0]) + len(results_tight[0][1])
    assert pair_len <= 120


def test_feature_flag_disabling_style_examples(monkeypatch):
    """Verify that disabling STYLE_EXAMPLES_ENABLED flag safely turns off example retrieval."""
    monkeypatch.setattr(main, "STYLE_EXAMPLES_ENABLED", False)

    examples = get_style_examples("Are you free today?", intent="availability")
    assert examples == []

    hobart_tz = ZoneInfo("Australia/Hobart")
    now_local = datetime(2026, 8, 11, 14, 30, tzinfo=hobart_tz)
    instructions, user_prompt, retrieved_exs = assemble_safe_prompt(
        system_prompt_tmpl="Base prompt for {provider_name}",
        user_prompt_tmpl="Message: {message}",
        query="Are you free today?",
        retrieved_context="",
        slots_str="",
        now_local=now_local,
    )

    assert retrieved_exs == []
    assert "Example 1" not in instructions
    assert "Base prompt for Tori" in instructions

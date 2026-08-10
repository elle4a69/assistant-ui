import json

from bootcamp import (
    DEFAULT_STYLE_PROFILE,
    BootcampStore,
    BootcampRunner,
    StyleProfileStore,
    clarification_for_handoff,
    load_opening_messages,
    normalize_style_profile,
    render_style_profile,
)


def test_style_profile_is_bounded_and_rendered_as_behavior():
    profile = normalize_style_profile({"flirtiness": 99, "sarcasm": -4})

    assert profile["flirtiness"] == 5
    assert profile["sarcasm"] == 0
    rendered = render_style_profile(profile)
    assert "confidently flirtatious" in rendered
    assert "Do not use sarcasm" in rendered


def test_style_profile_apply_and_undo_are_atomic(tmp_path):
    profiles = StyleProfileStore(tmp_path)
    original = profiles.get_active()
    assert profiles.get_applied() is None
    changed = {**original, "warmth": 1, "wit": 5}

    profiles.apply(changed)
    assert profiles.get_active()["warmth"] == 1
    assert profiles.get_active()["wit"] == 5

    restored = profiles.undo()
    assert restored == original
    assert profiles.get_active() == original
    assert profiles.get_applied() is None


def test_openings_are_loaded_only_from_user_messages(tmp_path):
    source = tmp_path / "dataset.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "Are you free tonight?"},
                            {"role": "assistant", "content": "Possibly."},
                        ]
                    }
                ),
                json.dumps(
                    {
                        "messages": [
                            {"role": "assistant", "content": "Wrong first role"},
                            {"role": "user", "content": "Ignore this"},
                        ]
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    assert load_opening_messages(source) == ["Are you free tonight?"]


def test_customer_answerable_uncertainty_becomes_a_clarifying_question():
    assert clarification_for_handoff(
        "They haven't said which service they mean.",
        "How much is it and how long does it take?",
    ) == "Which service were you interested in?"
    assert clarification_for_handoff(
        "Availability this week is not recorded.",
        "Do you have anything later this week?",
    ) == "What day and roughly what time were you thinking?"
    assert clarification_for_handoff(
        "Availability is not recorded.",
        "Are you free Friday at 8pm?",
    ) is None
    assert clarification_for_handoff(
        "Thursday evening availability is not recorded.",
        "Thursday, around 7 or 8—I just said that.",
    ) is None


def test_handoff_stores_no_customer_facing_reply(tmp_path):
    store = BootcampStore(tmp_path / "silent-handoff.db")
    runner = BootcampRunner(
        store,
        ["What is your cancellation policy?"],
        lambda history, profile: ("", "Cancellation policy is not recorded"),
        lambda persona, history, seed: seed or "Next",
        max_workers=1,
        message_delay_seconds=0,
    )

    run_id = runner.start(["cranky-carl"], 2, DEFAULT_STYLE_PROFILE)
    runner._threads[run_id].join(timeout=2)
    conversation = store.get_run(run_id)["conversations"][0]

    assert conversation["status"] == "handoff"
    assert conversation["needsHandoff"] is True
    assert [message["role"] for message in conversation["messages"]] == ["persona"]


def test_duplicate_tori_reply_is_silently_handed_off(tmp_path):
    store = BootcampStore(tmp_path / "duplicate-reply.db")
    runner = BootcampRunner(
        store,
        ["Are you free Thursday at 7?"],
        lambda history, profile: ("What day and roughly what time were you thinking?", None),
        lambda persona, history, seed: seed or "Thursday at 7, like I said.",
        max_workers=1,
        message_delay_seconds=0,
    )

    run_id = runner.start(["cranky-carl"], 3, DEFAULT_STYLE_PROFILE)
    runner._threads[run_id].join(timeout=2)
    conversation = store.get_run(run_id)["conversations"][0]

    assert conversation["status"] == "handoff"
    assert conversation["handoffReason"] == "Tori attempted to repeat the same reply"
    assert [message["role"] for message in conversation["messages"]] == [
        "persona", "tori", "persona"
    ]


def test_bootcamp_store_keeps_simulations_separate_and_persistent(tmp_path):
    store = BootcampStore(tmp_path / "bootcamp.db")
    run_id = store.create_run(["cranky-carl", "happy-harry"], 4, DEFAULT_STYLE_PROFILE)
    conversation_id = store.create_conversation(run_id, "cranky-carl", "Cranky Carl")
    store.add_message(conversation_id, "persona", "Why is this taking so long?")
    store.add_message(conversation_id, "tori", "What did you need to know?")

    snapshot = store.get_run(run_id)

    assert snapshot["status"] == "running"
    assert len(snapshot["conversations"]) == 1
    assert [message["role"] for message in snapshot["conversations"][0]["messages"]] == [
        "persona",
        "tori",
    ]
    assert store.count_messages() == 2


def test_bootcamp_message_speed_limit_is_bounded(tmp_path):
    runner = BootcampRunner(
        BootcampStore(tmp_path / "paced.db"),
        [],
        lambda history, profile: ("Reply", None),
        lambda persona, history, seed: seed or "Next",
        message_delay_seconds=99,
    )

    assert runner.message_delay_seconds == 10.0


def test_bootcamp_handoff_can_be_resolved_with_a_tori_retry(tmp_path):
    store = BootcampStore(tmp_path / "resolved-handoff.db")
    run_id = store.create_run(["cranky-carl"], 3, DEFAULT_STYLE_PROFILE)
    conversation_id = store.create_conversation(run_id, "cranky-carl", "Cranky Carl")
    store.add_message(conversation_id, "persona", "Do you allow couples?")
    store.update_conversation(
        conversation_id,
        status="handoff",
        needs_handoff=True,
        handoff_reason="Couples policy is not recorded",
    )

    pending = store.get_conversation(conversation_id)
    assert pending is not None
    assert pending["needsHandoff"] is True
    assert pending["styleProfile"] == DEFAULT_STYLE_PROFILE

    store.add_message(
        conversation_id,
        "tori",
        "Yes, couples are welcome.",
        {"source": "information-request"},
    )
    store.resolve_handoff(conversation_id)
    resolved = store.get_conversation(conversation_id)

    assert resolved is not None
    assert resolved["status"] == "completed"
    assert resolved["needsHandoff"] is False
    assert resolved["handoffReason"] is None
    assert resolved["messages"][-1]["text"] == "Yes, couples are welcome."

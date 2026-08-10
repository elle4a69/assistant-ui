import main
from bootcamp import BootcampStore, DEFAULT_STYLE_PROFILE
from main import BootcampInformationRequestInput, respond_to_bootcamp_information_request


def test_bootcamp_information_request_saves_lesson_and_retries(monkeypatch, tmp_path):
    store = BootcampStore(tmp_path / "bootcamp-information.db")
    run_id = store.create_run(["cranky-carl"], 4, DEFAULT_STYLE_PROFILE)
    conversation_id = store.create_conversation(run_id, "cranky-carl", "Cranky Carl")
    customer_message_id = store.add_message(
        conversation_id,
        "persona",
        "Do you offer a couples service?",
    )
    store.update_conversation(
        conversation_id,
        status="handoff",
        current_turn=1,
        needs_handoff=True,
        handoff_reason="The couples policy is not recorded",
    )

    monkeypatch.setattr(main, "BOOTCAMP_STORE", store)
    monkeypatch.setattr(main, "generate_bootcamp_information_resolution", lambda *_args: {
        "customer_reply": "Yes, couples are welcome. What day were you thinking?",
        "knowledge_summary": "Couples are accepted for the couples service.",
    })
    saved = []
    monkeypatch.setattr(main, "save_learned_information", lambda *args: saved.append(args) or "learned_information.jsonl")

    result = respond_to_bootcamp_information_request(
        conversation_id,
        BootcampInformationRequestInput(
            information="Yes, couples are welcome for the couples service."
        ),
    )

    conversation = store.get_conversation(conversation_id)
    assert conversation is not None
    assert result["status"] == "success"
    assert conversation["needsHandoff"] is False
    assert conversation["handoffReason"] is None
    assert conversation["status"] == "completed"
    assert conversation["messages"][-1]["role"] == "tori"
    assert conversation["messages"][-1]["text"] == "Yes, couples are welcome. What day were you thinking?"
    assert conversation["messages"][-1]["meta"]["source"] == "information-request"
    assert saved[0][0] == f"bootcamp-{conversation_id}-{customer_message_id}"
    assert saved[0][1] == "Do you offer a couples service?"


def test_bootcamp_information_request_cannot_be_submitted_twice(monkeypatch, tmp_path):
    store = BootcampStore(tmp_path / "bootcamp-resolved.db")
    run_id = store.create_run(["cranky-carl"], 2, DEFAULT_STYLE_PROFILE)
    conversation_id = store.create_conversation(run_id, "cranky-carl", "Cranky Carl")
    store.add_message(conversation_id, "persona", "Question")
    store.update_conversation(conversation_id, status="completed")
    monkeypatch.setattr(main, "BOOTCAMP_STORE", store)

    try:
        respond_to_bootcamp_information_request(
            conversation_id,
            BootcampInformationRequestInput(information="Answer"),
        )
    except main.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("Expected a resolved request to be rejected")

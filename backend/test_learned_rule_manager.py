import json

import main


def test_learned_rule_manager_lists_updates_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    entry = {
        "id": "line-two-rule",
        "topic": "Initial topic",
        "text": "Initial guidance",
        "scope": "secondary",
        "created_at": "2026-08-26T00:00:00Z",
    }
    (tmp_path / main.LEARNED_INFORMATION_FILENAME).write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )

    assert main.list_learned_information()[0]["scope"] == "secondary"

    updated = main.replace_learned_information_entry(
        "line-two-rule",
        {"topic": "Updated topic", "text": "Updated guidance", "scope": "shared"},
    )
    assert updated["topic"] == "Updated topic"
    assert updated["text"] == "Updated guidance"
    assert updated["scope"] == "shared"

    main.delete_learned_information_entry("line-two-rule")
    assert main.list_learned_information() == []


import main

def test_line_scoped_learning_is_never_retrieved_by_the_other_line(monkeypatch):
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [
        {"source": "learned_information.jsonl", "type": "text", "text": "Line 2-only service fact", "scope": "secondary", "retrieval_enabled": True},
        {"source": "learned_information.jsonl", "type": "text", "text": "A shared generic courtesy rule", "scope": "shared", "retrieval_enabled": True},
    ])
    primary = main.search_knowledge("service courtesy", account_key="primary")
    secondary = main.search_knowledge("service courtesy", account_key="secondary")
    assert "Line 2-only service fact" not in primary
    assert "shared generic courtesy rule" in primary
    assert "Line 2-only service fact" in secondary

def test_information_request_learning_is_forced_to_its_active_line(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "classify_knowledge_entries", lambda entries: {entries[0]["id"]: {"scope": "shared", "retrieval_enabled": True}})
    monkeypatch.setattr(main, "_upsert_learned_information_entry", lambda entry: captured.update(entry))
    main.save_learned_information("request-1", "Question", "Owner answer", "Durable fact", "secondary")
    assert captured["scope"] == "secondary"
    assert captured["source_account_key"] == "secondary"

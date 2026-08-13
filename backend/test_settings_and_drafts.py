import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, SettingsUpdateInput, Thread, ThreadEvent, find_oldest_catch_up_candidate


class FakeLearningResponses:
    def __init__(self, output_text):
        self.output_text = output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("LearningResponse", (), {"output_text": self.output_text})()


class FakeLearningClient:
    def __init__(self, output_text):
        self.responses = FakeLearningResponses(output_text)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_thread(db, thread_id: str, state: str = "needs-review"):
    now = datetime.utcnow()
    db.add(Thread(
        id=thread_id,
        customer_phone=f"+6140000{thread_id[-3:]}",
        state=state,
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    ))


def test_avatar_setting_can_be_saved_as_false(monkeypatch, tmp_path):
    settings_path = tmp_path / "message_ui_settings.json"
    monkeypatch.setattr(main, "MESSAGE_UI_SETTINGS_PATH", str(settings_path))

    result = main.update_settings(SettingsUpdateInput(showMessageAvatars=False))

    assert result == {"status": "success"}
    assert main.load_message_ui_settings() == {"showMessageAvatars": False}
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {"showMessageAvatars": False}


def test_legacy_first_contact_settings_migrate_to_tori_only(monkeypatch, tmp_path):
    settings_path = tmp_path / "first_contact_autoresponder.json"
    settings_path.write_text(json.dumps({
        "enabled": True,
        "cooldownDays": 45,
        "delaySeconds": 12,
        "message": "Existing Tori greeting",
    }), encoding="utf-8")
    monkeypatch.setattr(main, "FIRST_CONTACT_AUTORESPONDER_PATH", str(settings_path))

    accounts = main.load_first_contact_autoresponders()

    assert accounts["primary"] == {
        "enabled": True,
        "cooldownDays": 45,
        "delaySeconds": 12,
        "message": "Existing Tori greeting",
    }
    assert accounts["secondary"] == main.FIRST_CONTACT_AUTORESPONDER_DEFAULT


def test_first_contact_settings_save_independent_accounts(monkeypatch, tmp_path):
    settings_path = tmp_path / "first_contact_autoresponder.json"
    monkeypatch.setattr(main, "FIRST_CONTACT_AUTORESPONDER_PATH", str(settings_path))
    payload = main.FirstContactAutoresponderAccountsInput(accounts={
        "primary": main.FirstContactAutoresponderInput(
            enabled=True, cooldownDays=30, delaySeconds=5, message="Tori hello",
        ),
        "secondary": main.FirstContactAutoresponderInput(
            enabled=True, cooldownDays=7, delaySeconds=20, message="Anonymous hello",
        ),
    })

    result = main.save_first_contact_autoresponder(payload)
    saved = json.loads(settings_path.read_text(encoding="utf-8"))

    assert result["status"] == "success"
    assert saved["accounts"]["primary"]["message"] == "Tori hello"
    assert saved["accounts"]["secondary"]["message"] == "Anonymous hello"


def test_clear_pending_drafts_removes_all_drafts_and_updates_threads():
    db = make_db()
    add_thread(db, "thread-001")
    add_thread(db, "thread-002")
    db.add_all([
        Message(id="customer-1", thread_id="thread-001", role="customer", text="Please reply", at=datetime.utcnow()),
        Message(id="draft-1", thread_id="thread-001", role="draft", text="First", at=datetime.utcnow()),
        Message(id="draft-2", thread_id="thread-001", role="draft", text="Second", at=datetime.utcnow()),
        Message(id="customer-2", thread_id="thread-002", role="customer", text="Already handled", at=datetime.utcnow()),
        Message(id="draft-3", thread_id="thread-002", role="draft", text="Third", at=datetime.utcnow()),
        Message(id="agent-1", thread_id="thread-002", role="agent", text="Keep me", at=datetime.utcnow()),
    ])
    db.commit()

    result = main.clear_pending_draft_messages(db)

    assert result == {"status": "success", "removedDrafts": 3, "affectedThreads": 2}
    assert db.query(Message).filter(Message.role == "draft").count() == 0
    assert db.query(Message).filter(Message.id == "agent-1").count() == 1
    assert {thread.state for thread in db.query(Thread).all()} == {"auto-reply"}
    events = db.query(ThreadEvent).filter(ThreadEvent.type == "drafts-cleared").all()
    assert len(events) == 2
    assert sorted(json.loads(event.meta)["count"] for event in events) == [1, 2]
    retry_thread, retry_message = find_oldest_catch_up_candidate(db)
    assert retry_thread.id == "thread-001"
    assert retry_message.id == "customer-1"
    assert main.clear_pending_draft_messages(db) == {
        "status": "success",
        "removedDrafts": 0,
        "affectedThreads": 0,
    }
    db.close()


def test_manual_learning_is_ai_structured_saved_and_reindexed(monkeypatch, tmp_path):
    client = FakeLearningClient(json.dumps({
        "topic": "Customer changes a requested booking time",
        "applies_when": "A customer asks to move a booking that belongs to them.",
        "instruction": "Check the customer's existing booking before offering replacement times.",
        "example_reply": "I can check whether that new time is available for you.",
    }))
    monkeypatch.setattr(main, "openai_client", client)
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [])

    result = main.create_manual_learning(main.ManualLearningInput(
        topic="changing booking times",
        guidance="Check that it is their booking first, then offer the closest valid time.",
    ))

    lines = (tmp_path / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8").splitlines()
    saved = json.loads(lines[0])
    assert result["status"] == "success"
    assert result["filename"] == main.LEARNED_INFORMATION_FILENAME
    assert saved["type"] == "manual_guidance"
    assert saved["owner_topic"] == "changing booking times"
    assert saved["owner_guidance"] == "Check that it is their booking first, then offer the closest valid time."
    assert "Instruction: Check the customer's existing booking" in saved["text"]
    assert client.responses.calls[0]["store"] is False
    assert main.retrieve_knowledge_chunks("change booking time")[0]["source"] == main.LEARNED_INFORMATION_FILENAME


def test_manual_learning_fails_closed_when_ai_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "openai_client", None)
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))

    with pytest.raises(main.HTTPException) as exc_info:
        main.create_manual_learning(main.ManualLearningInput(
            topic="a topic",
            guidance="some guidance",
        ))

    assert exc_info.value.status_code == 503
    assert not (tmp_path / main.LEARNED_INFORMATION_FILENAME).exists()


def test_manual_learning_saves_nothing_for_invalid_ai_json(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "openai_client", FakeLearningClient("not valid json"))
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))

    with pytest.raises(main.HTTPException) as exc_info:
        main.create_manual_learning(main.ManualLearningInput(
            topic="a topic",
            guidance="some guidance",
        ))

    assert exc_info.value.status_code == 502
    assert not (tmp_path / main.LEARNED_INFORMATION_FILENAME).exists()

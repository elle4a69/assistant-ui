import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, SettingsUpdateInput, Thread, ThreadEvent, find_oldest_catch_up_candidate


class FakeLearningResponses:
    def __init__(self, output_text):
        self.output_text = list(output_text) if isinstance(output_text, list) else output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output_text = self.output_text.pop(0) if isinstance(self.output_text, list) else self.output_text
        if callable(output_text):
            output_text = output_text(kwargs)
        return type("LearningResponse", (), {"output_text": output_text})()


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
    assert main.load_message_ui_settings() == {
        "showMessageAvatars": False,
        "catchUpLookbackDays": main.DEFAULT_CATCH_UP_LOOKBACK_DAYS,
    }
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "showMessageAvatars": False,
        "catchUpLookbackDays": main.DEFAULT_CATCH_UP_LOOKBACK_DAYS,
    }


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


def test_clear_review_only_threads_preserves_pending_drafts():
    db = make_db()
    add_thread(db, "thread-review-only")
    add_thread(db, "thread-with-draft")
    add_thread(db, "thread-auto", state="auto-reply")
    db.add_all([
        Message(id="draft-keep", thread_id="thread-with-draft", role="draft", text="Review me", at=datetime.utcnow()),
        Message(id="customer-auto", thread_id="thread-auto", role="customer", text="Keep state", at=datetime.utcnow()),
    ])
    db.commit()

    result = main.clear_review_only_threads(db)

    assert result == {"status": "success", "clearedThreads": 1, "draftReviewThreads": 1}
    assert db.query(Message).filter(Message.id == "draft-keep").count() == 1
    assert db.query(Thread).filter(Thread.id == "thread-review-only").one().state == "auto-reply"
    assert db.query(Thread).filter(Thread.id == "thread-with-draft").one().state == "needs-review"
    assert db.query(Thread).filter(Thread.id == "thread-auto").one().state == "auto-reply"
    event = db.query(ThreadEvent).filter(ThreadEvent.type == "review-status-cleared").one()
    assert event.thread_id == "thread-review-only"
    assert main.clear_review_only_threads(db) == {
        "status": "success", "clearedThreads": 0, "draftReviewThreads": 1,
    }
    db.close()


def test_edited_draft_is_saved_as_classified_learning_after_approval(monkeypatch, tmp_path):
    client = FakeLearningClient(lambda kwargs: json.dumps({"classifications": [{
        "id": json.loads(kwargs["input"])["records"][0]["id"],
        "scope": "shared",
        "category": "service_specific",
        "retrieval_enabled": True,
    }]}))
    monkeypatch.setattr(main, "openai_client", client)
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [])
    db = make_db()
    add_thread(db, "thread-101")
    thread = db.query(Thread).filter(Thread.id == "thread-101").one()
    thread.customer_phone = "locanto_learning_test"
    now = datetime.utcnow()
    db.add_all([
        Message(id="customer-learning", thread_id=thread.id, role="customer", text="Do you offer the natural service?", at=now),
        Message(id="draft-learning", thread_id=thread.id, role="draft", text="Initial draft", at=now + timedelta(seconds=1)),
    ])
    db.commit()

    main.update_draft_message(
        "draft-learning",
        main.DraftUpdateInput(text="Yes, natural service is available. Have a look at the service page too."),
        db,
    )
    result = main.approve_draft_message("draft-learning", db)

    saved = json.loads((tmp_path / main.LEARNED_INFORMATION_FILENAME).read_text(encoding="utf-8").strip())
    assert result["status"] == "success"
    assert result["learningSaved"] is True
    assert saved["type"] == "staff_edited_draft"
    assert saved["retrieval_enabled"] is True
    assert saved["example_reply"].startswith("Yes, natural service")
    assert db.query(Message).filter(Message.id == "draft-learning").one().role == "agent"
    db.close()


def test_unedited_draft_is_not_added_to_learning(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(tmp_path))
    db = make_db()
    add_thread(db, "thread-102")
    thread = db.query(Thread).filter(Thread.id == "thread-102").one()
    thread.customer_phone = "locanto_unedited_test"
    now = datetime.utcnow()
    db.add_all([
        Message(id="customer-unedited", thread_id=thread.id, role="customer", text="Hello", at=now),
        Message(id="draft-unedited", thread_id=thread.id, role="draft", text="Hi there", at=now + timedelta(seconds=1)),
    ])
    db.commit()

    result = main.approve_draft_message("draft-unedited", db)

    assert result["learningSaved"] is False
    assert not (tmp_path / main.LEARNED_INFORMATION_FILENAME).exists()
    db.close()


def test_manual_learning_is_ai_structured_saved_and_reindexed(monkeypatch, tmp_path):
    client = FakeLearningClient([
        json.dumps({
            "topic": "Customer changes a requested booking time",
            "applies_when": "A customer asks to move a booking that belongs to them.",
            "instruction": "Check the customer's existing booking before offering replacement times.",
            "example_reply": "I can check whether that new time is available for you.",
        }),
        lambda kwargs: json.dumps({"classifications": [{
            "id": json.loads(kwargs["input"])["records"][0]["id"],
            "scope": "shared", "category": "generic", "retrieval_enabled": True,
        }]}),
    ])
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


import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, SettingsUpdateInput, Thread, ThreadEvent, find_oldest_catch_up_candidate


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
    assert {thread.state for thread in db.query(Thread).all()} == {"taken-over"}
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

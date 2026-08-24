import json
from datetime import datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, Thread, ThreadEvent, WebhookSMSInput, webhook_sms


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_thread(db, thread_id, state="auto-reply"):
    now = datetime.utcnow()
    thread = Thread(
        id=thread_id,
        customer_phone=f"+6140000{thread_id[-3:]}",
        sms_account_key="primary",
        state=state,
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.commit()
    return thread


def add_reply_event(db, thread_id, event_id, at, event_type="auto-reply-sent", agent_id=None):
    db.add(ThreadEvent(
        id=event_id,
        thread_id=thread_id,
        type=event_type,
        agent_id=agent_id,
        meta=json.dumps({}),
        at=at,
    ))


def receive(db, phone, message_id):
    return webhook_sms(
        WebhookSMSInput.model_validate({
            "sender": phone,
            "message": "Can someone help me?",
            "message_id": message_id,
            "received_at": datetime.utcnow().isoformat(),
        }),
        BackgroundTasks(),
        db,
    )


def disable_first_contact(monkeypatch):
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)
    monkeypatch.setattr(
        main,
        "load_first_contact_autoresponder",
        lambda _account="primary": {
            "enabled": False,
            "cooldownDays": 30,
            "delaySeconds": 0,
            "message": "",
        },
    )


def test_needs_review_thread_waits_for_human_instead_of_auto_replying(monkeypatch):
    db = make_db()
    thread = add_thread(db, "review-001", state="needs-review")
    disable_first_contact(monkeypatch)
    generated = []
    monkeypatch.setattr(
        main,
        "run_sms_reply_logic",
        lambda *_args, **_kwargs: generated.append(True) or (False, False),
    )

    result = receive(db, thread.customer_phone, "review-inbound")

    assert result["human_follow_up_required"] is True
    assert result["auto_reply_skipped_reason"] == "needs-review"
    assert generated == []
    assert db.get(Thread, thread.id).state == "needs-review"
    event = db.query(ThreadEvent).filter(ThreadEvent.type == "ai-reply-skipped").one()
    assert json.loads(event.meta)["reason"] == "needs-review"
    db.close()


def test_third_auto_reply_is_blocked_and_thread_is_queued_for_human(monkeypatch):
    db = make_db()
    thread = add_thread(db, "limit-002")
    now = datetime.utcnow()
    add_reply_event(db, thread.id, "auto-1", now - timedelta(minutes=4))
    add_reply_event(db, thread.id, "auto-2", now - timedelta(minutes=2))
    db.commit()
    disable_first_contact(monkeypatch)
    generated = []
    monkeypatch.setattr(
        main,
        "run_sms_reply_logic",
        lambda *_args, **_kwargs: generated.append(True) or (False, False),
    )

    result = receive(db, thread.customer_phone, "limit-inbound")

    assert main.consecutive_auto_replies_without_human(db, thread.id) == 2
    assert result["auto_reply_skipped_reason"] == "consecutive-auto-reply-limit"
    assert generated == []
    assert db.get(Thread, thread.id).state == "needs-review"
    assert db.query(ThreadEvent).filter(ThreadEvent.type == "auto-reply-sent").count() == 2
    db.close()


def test_human_reply_resets_the_auto_reply_limit(monkeypatch):
    db = make_db()
    thread = add_thread(db, "reset-003")
    now = datetime.utcnow()
    add_reply_event(db, thread.id, "old-auto-1", now - timedelta(minutes=8))
    add_reply_event(db, thread.id, "old-auto-2", now - timedelta(minutes=7))
    add_reply_event(
        db,
        thread.id,
        "human-reply",
        now - timedelta(minutes=6),
        event_type="human-reply-sent",
        agent_id="owner",
    )
    add_reply_event(db, thread.id, "new-auto-1", now - timedelta(minutes=4))
    db.commit()
    disable_first_contact(monkeypatch)
    generated = []
    monkeypatch.setattr(
        main,
        "run_sms_reply_logic",
        lambda *_args, **_kwargs: generated.append(True) or (False, False),
    )

    result = receive(db, thread.customer_phone, "reset-inbound")

    assert main.consecutive_auto_replies_without_human(db, thread.id) == 1
    assert result.get("human_follow_up_required") is None
    assert generated == [True]
    assert db.get(Thread, thread.id).state == "auto-reply"
    db.close()

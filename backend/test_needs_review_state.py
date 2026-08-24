import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, OperationsAction, Thread, ThreadEvent


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False)()


def add_thread(db, thread_id, *, pending_booking=None):
    now = datetime.utcnow()
    fixture_number = uuid.uuid5(uuid.NAMESPACE_URL, thread_id).int % 100_000_000
    thread = Thread(
        id=thread_id,
        customer_phone=f"+614{fixture_number:08d}",
        state="needs-review",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        pending_booking=pending_booking,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    return thread


def add_event(db, thread, event_type, offset):
    db.add(ThreadEvent(
        id=f"{thread.id}-{event_type}-{offset}",
        thread_id=thread.id,
        type=event_type,
        at=datetime(2026, 1, 1) + timedelta(seconds=offset),
        meta="{}",
    ))


def test_bounded_operations_repair_clears_only_non_actionable_review_flags():
    db = make_db()
    stale_outcomes = [
        "ai-reply-cancelled",
        "ai-reply-skipped",
        "auto-reply-sent",
        "resolution",
        "drafts-cleared",
    ]
    for index, outcome in enumerate(stale_outcomes):
        thread = add_thread(db, f"stale-{index}")
        add_event(db, thread, "ai-reply-failed", 1)
        add_event(db, thread, outcome, 2)

    failed = add_thread(db, "keep-failure")
    add_event(db, failed, "ai-reply-failed", 3)

    drafted = add_thread(db, "keep-draft")
    add_event(db, drafted, "auto-reply-sent", 4)
    db.add(Message(
        id="pending-draft",
        thread_id=drafted.id,
        role="draft",
        text="An anonymised pending reply",
        at=datetime(2026, 1, 1, 0, 0, 5),
    ))

    proposed = add_thread(
        db,
        "keep-proposal",
        pending_booking=json.dumps({"service_name": "Example service"}),
    )
    add_event(db, proposed, "auto-reply-sent", 5)
    db.commit()

    first = main.process_needs_review_repair_batch(db, batch_size=2)
    assert first["processed"] == 2
    assert first["batch_limit"] == 2
    assert first["status"] == "queued"

    result = first
    while result["status"] != "completed":
        result = main.process_needs_review_repair_batch(db, batch_size=2)

    states = {thread.id: thread.state for thread in db.query(Thread).all()}
    assert {states[f"stale-{index}"] for index in range(len(stale_outcomes))} == {"auto-reply"}
    assert states["keep-failure"] == "needs-review"
    assert states["keep-draft"] == "needs-review"
    assert states["keep-proposal"] == "needs-review"
    assert result["scanned"] == len(stale_outcomes) + 3
    assert result["cleared"] == len(stale_outcomes)

    repeated = main.process_needs_review_repair_batch(db, batch_size=2)
    assert repeated["status"] == "completed"
    assert repeated["processed"] == 0
    assert db.query(OperationsAction).filter(
        OperationsAction.action_type == main.NEEDS_REVIEW_REPAIR_ACTION_TYPE,
    ).count() == 1
    db.close()


def test_terminal_outcomes_do_not_recreate_review_but_actionable_items_remain_listed():
    db = make_db()
    for index, outcome in enumerate([
        "ai-reply-cancelled",
        "ai-reply-skipped",
        "auto-reply-sent",
        "human-reply-sent",
        "draft-discarded",
    ]):
        thread = add_thread(db, f"terminal-{index}")
        add_event(db, thread, "ai-reply-failed", 10)
        add_event(db, thread, outcome, 11)
        assert main.sync_thread_needs_review(db, thread, mark_actionable=True) is False
        assert thread.state == "auto-reply"
        assert main.sync_thread_needs_review(db, thread, mark_actionable=True) is False
        assert thread.state == "auto-reply"

    failed = add_thread(db, "action-failure")
    add_event(db, failed, "ai-reply-failed", 20)
    assert main.sync_thread_needs_review(db, failed, mark_actionable=True) is True

    drafted = add_thread(db, "action-draft")
    db.add(Message(
        id="action-draft-message",
        thread_id=drafted.id,
        role="draft",
        text="An anonymised pending draft",
        at=datetime(2026, 1, 1),
    ))
    add_event(db, drafted, "auto-reply-sent", 21)
    assert main.sync_thread_needs_review(db, drafted, mark_actionable=True) is True

    proposal = add_thread(
        db,
        "action-proposal",
        pending_booking=json.dumps({"service_name": "Example service"}),
    )
    add_event(db, proposal, "auto-reply-sent", 22)
    assert main.sync_thread_needs_review(db, proposal, mark_actionable=True) is True
    db.commit()

    listed = main.get_threads(None, "needs-review", None, None, db)
    assert {item["id"] for item in listed} == {
        "action-failure",
        "action-draft",
        "action-proposal",
    }
    db.close()


def test_superseded_generation_clears_a_prior_non_actionable_failure(monkeypatch):
    db = make_db()
    thread = add_thread(db, "superseded-flow")
    add_event(db, thread, "ai-reply-failed", 1)
    old_at = datetime.utcnow() - timedelta(seconds=2)
    new_at = datetime.utcnow() - timedelta(seconds=1)
    old_message = Message(
        id="superseded-old",
        thread_id=thread.id,
        role="customer",
        text="First anonymised fragment",
        provider_message_id="provider-old",
        at=old_at,
    )
    new_message = Message(
        id="superseded-new",
        thread_id=thread.id,
        role="customer",
        text="Newer anonymised fragment",
        provider_message_id="provider-new",
        at=new_at,
    )
    db.add_all([old_message, new_message])
    db.commit()
    monkeypatch.setattr(main, "account_allows_conversational_ai", lambda _account: True)

    result = main.run_sms_reply_logic(
        db,
        thread.id,
        old_message.text,
        old_message.provider_message_id,
        old_message.at,
    )

    db.refresh(thread)
    assert result == (False, False)
    assert thread.state == "auto-reply"
    assert db.query(ThreadEvent).filter(
        ThreadEvent.thread_id == thread.id,
        ThreadEvent.type == "ai-reply-cancelled",
    ).count() == 1
    db.close()

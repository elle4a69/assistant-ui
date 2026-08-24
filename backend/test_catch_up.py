from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import (
    Base,
    CatchUpClaim,
    CatchUpCursor,
    Message,
    Thread,
    ThreadEvent,
    WebhookSMSInput,
    find_oldest_catch_up_candidate,
    process_catch_up_batch,
)


def make_thread(db, thread_id, phone, state="auto-reply", enabled=True):
    thread = Thread(
        id=thread_id,
        customer_phone=phone,
        state=state,
        priority="medium",
        sla_due_at=datetime.utcnow() + timedelta(hours=1),
        unread_count=1,
        auto_reply_enabled=enabled,
    )
    db.add(thread)


def add_message(db, message_id, thread_id, role, at):
    db.add(Message(id=message_id, thread_id=thread_id, role=role, text=message_id, at=at))


def test_catch_up_selects_oldest_unanswered_and_skips_answered_or_managed():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()

    make_thread(db, "old", "+1001")
    add_message(db, "old-customer", "old", "customer", now - timedelta(minutes=10))

    make_thread(db, "new", "+1002")
    add_message(db, "new-customer", "new", "customer", now - timedelta(minutes=5))

    make_thread(db, "answered", "+1003")
    add_message(db, "answered-customer", "answered", "customer", now - timedelta(minutes=20))
    add_message(db, "answered-agent", "answered", "agent", now - timedelta(minutes=19))

    make_thread(db, "managed", "+1004", state="taken-over")
    add_message(db, "managed-customer", "managed", "customer", now - timedelta(minutes=30))
    db.add(ThreadEvent(
        id="explicit-takeover",
        thread_id="managed",
        type="takeover",
        agent_id="operator",
        meta="{}",
        at=now - timedelta(minutes=31),
    ))
    db.commit()

    thread, message = find_oldest_catch_up_candidate(db)
    assert thread.id == "old"
    assert message.id == "old-customer"


def test_catch_up_recovers_old_automatically_stranded_taken_over_thread():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()

    make_thread(db, "stranded", "+1101", state="taken-over")
    add_message(db, "stranded-customer", "stranded", "customer", now - timedelta(days=2))
    db.add(ThreadEvent(
        id="old-draft-clear",
        thread_id="stranded",
        type="drafts-cleared",
        agent_id="bulk-discard",
        meta='{"count":1}',
        at=now - timedelta(days=3),
    ))
    db.add(ThreadEvent(
        id="older-explicit-takeover",
        thread_id="stranded",
        type="takeover",
        agent_id="operator",
        meta="{}",
        at=now - timedelta(days=4),
    ))
    db.commit()

    thread, message = find_oldest_catch_up_candidate(db)
    assert thread.id == "stranded"
    assert message.id == "stranded-customer"
    db.close()


def test_catch_up_returns_none_when_only_drafts_or_disabled_threads_remain():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()

    make_thread(db, "drafted", "+2001", state="needs-review")
    add_message(db, "customer", "drafted", "customer", now - timedelta(minutes=2))
    add_message(db, "draft", "drafted", "draft", now - timedelta(minutes=1))

    make_thread(db, "disabled", "+2002", enabled=False)
    add_message(db, "disabled-customer", "disabled", "customer", now)
    db.commit()

    assert find_oldest_catch_up_candidate(db) is None


def test_new_inbound_restores_only_automatic_taken_over_state(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()

    make_thread(db, "automatic", "+61400000101", state="taken-over", enabled=False)
    make_thread(db, "explicit", "+61400000102", state="taken-over", enabled=False)
    db.add(ThreadEvent(
        id="explicit-event",
        thread_id="explicit",
        type="takeover",
        agent_id="operator",
        meta="{}",
        at=now,
    ))
    db.commit()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)

    for suffix, phone in (("automatic", "+61400000101"), ("explicit", "+61400000102")):
        main.webhook_sms(
            WebhookSMSInput.model_validate({
                "from": phone,
                "body": "New inbound",
                "providerMessageId": f"provider-{suffix}",
                "receivedAt": now.isoformat(),
            }),
            main.BackgroundTasks(),
            db,
        )

    assert db.get(Thread, "automatic").state == "auto-reply"
    assert db.get(Thread, "explicit").state == "taken-over"
    assert db.get(Thread, "automatic").auto_reply_enabled is False
    db.close()


def test_catch_up_batch_filters_resumes_and_deduplicates(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    make_thread(db, "first", "+3001")
    add_message(db, "first-customer", "first", "customer", now - timedelta(minutes=10))
    make_thread(db, "second", "+3002")
    add_message(db, "second-customer", "second", "customer", now - timedelta(minutes=9))
    make_thread(db, "global-off", "+3003")
    add_message(db, "off-customer", "global-off", "customer", now - timedelta(minutes=8))
    db.add(ThreadEvent(
        id="missed-event",
        thread_id="global-off",
        type="ai-reply-missed",
        agent_id=None,
        meta='{"message_id":"off-customer","reason":"global-ai-off"}',
        at=now,
    ))
    make_thread(db, "disabled", "+3004", enabled=False)
    add_message(db, "disabled-customer", "disabled", "customer", now - timedelta(minutes=7))
    make_thread(db, "answered", "+3005")
    add_message(db, "answered-customer", "answered", "customer", now - timedelta(minutes=12))
    add_message(db, "answered-agent", "answered", "agent", now - timedelta(minutes=11))
    db.commit()

    calls = []

    def fake_reply(db_arg, thread_id, body, provider_message_id, received_at, **kwargs):
        calls.append(kwargs)
        db_arg.add(Message(
            id=f"draft-{thread_id}",
            thread_id=thread_id,
            role="draft",
            text="Safe draft",
            at=received_at + timedelta(seconds=1),
        ))
        thread = db_arg.query(Thread).filter(Thread.id == thread_id).first()
        thread.state = "needs-review"
        db_arg.commit()
        return False, False

    monkeypatch.setattr(main, "run_sms_reply_logic", fake_reply)
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)

    first = process_catch_up_batch(db, max_threads=1, now=now, yield_fn=lambda: None)
    assert first["processed"] == 1
    assert calls[0] == {
        "dispatch_sms": False,
        "draft_only": True,
        "history_message_limit": main.CATCH_UP_MAX_MESSAGES_PER_THREAD,
    }
    cursor = db.get(CatchUpCursor, "primary")
    assert cursor.last_thread_id == "first"

    second = process_catch_up_batch(db, max_threads=10, now=now, yield_fn=lambda: None)
    assert second["processed"] == 1
    assert [item["processed"] for item in (first, second)] == [1, 1]
    assert {claim.message_id for claim in db.query(CatchUpClaim).all()} == {
        "first-customer", "second-customer",
    }

    again = process_catch_up_batch(db, max_threads=10, now=now, yield_fn=lambda: None)
    assert again["processed"] == 0
    assert len(calls) == 2


def test_catch_up_stops_on_budget_and_yields_between_items(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    for index in range(3):
        thread_id = f"budget-{index}"
        make_thread(db, thread_id, f"+400{index}")
        add_message(db, f"message-{index}", thread_id, "customer", now - timedelta(minutes=10-index))
    db.commit()

    calls = []
    def fake_reply(db_arg, thread_id, body, provider_message_id, received_at, **kwargs):
        calls.append(thread_id)
        db_arg.add(Message(
            id=f"draft-{thread_id}", thread_id=thread_id, role="draft", text="draft",
            at=received_at + timedelta(seconds=1),
        ))
        db_arg.commit()
        return False, False

    elapsed = [0.0]
    monkeypatch.setattr(main, "run_sms_reply_logic", fake_reply)
    result = process_catch_up_batch(
        db,
        budget_seconds=0.5,
        clock=lambda: elapsed[0],
        yield_fn=lambda: elapsed.__setitem__(0, elapsed[0] + 1.0),
        now=now,
    )
    assert result["stoppedOnBudget"] is True
    assert result["processed"] == 1
    assert calls == ["budget-0"]

    resumed = process_catch_up_batch(db, now=now, yield_fn=lambda: None)
    assert resumed["processed"] == 2
    assert calls == ["budget-0", "budget-1", "budget-2"]


def test_catch_up_failure_is_reported_to_operations(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    make_thread(db, "failure", "+5001")
    add_message(db, "failure-message", "failure", "customer", now - timedelta(minutes=10))
    db.commit()

    monkeypatch.setattr(main, "run_sms_reply_logic", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    result = process_catch_up_batch(db, now=now, yield_fn=lambda: None)
    report = main._operations_recent_failures(db, 10)
    assert result["outcome"] == "failed"
    assert report["catch_up"][0]["status"] == "failed"
    assert report["catch_up"][0]["last_error"] == "RuntimeError"
    assert any(event["type"] == "catch-up-failed" for event in report["events"])

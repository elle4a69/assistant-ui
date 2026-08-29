import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import (
    Base,
    Message,
    Thread,
    ThreadEvent,
    WebhookSMSInput,
    catch_up_missed_messages,
    find_oldest_catch_up_candidate,
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


def test_catch_up_skips_messages_older_than_configured_lookback(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    make_thread(db, "too-old", "+2011")
    add_message(db, "too-old-message", "too-old", "customer", now - timedelta(days=2))
    make_thread(db, "recent", "+2012")
    add_message(db, "recent-message", "recent", "customer", now - timedelta(hours=4))
    db.commit()
    monkeypatch.setattr(main, "load_message_ui_settings", lambda: {
        "showMessageAvatars": True, "catchUpLookbackDays": 1,
    })

    thread, message = find_oldest_catch_up_candidate(db)

    assert thread.id == "recent"
    assert message.id == "recent-message"
    db.close()


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


def test_catch_up_endpoint_sends_one_reply_for_recent_message(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    make_thread(db, "waiting", "+3001")
    add_message(db, "waiting-customer", "waiting", "customer", now)
    db.add(ThreadEvent(
        id="missed-event",
        thread_id="waiting",
        type="ai-reply-missed",
        agent_id=None,
        meta='{"message_id":"waiting-customer","reason":"global-ai-off"}',
        at=now,
    ))
    db.commit()

    model_calls = []
    sms_calls = []

    class FakeResponses:
        def create(self, **kwargs):
            model_calls.append(kwargs)
            return type("Response", (), {
                "output": [],
                "output_text": "Safe reply",
            })()

    monkeypatch.setattr(
        main,
        "openai_client",
        type("Client", (), {"responses": FakeResponses()})(),
    )
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_business_context", lambda _body: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *args, **kwargs: sms_calls.append((args, kwargs)) or {"status": "success"},
    )
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)

    result = catch_up_missed_messages(db)
    assert result == {
        "processed": True,
        "threadId": "waiting",
        "outcome": "sent",
        "remaining": 0,
    }
    assert len(model_calls) == 1
    assert sms_calls[0][0][:2] == ("+3001", "Safe reply")
    assert db.query(Message).filter_by(thread_id="waiting", role="system", text="Safe reply").count() == 1
    assert catch_up_missed_messages(db) == {
        "processed": False,
        "outcome": "complete",
        "remaining": 0,
    }
    db.close()


def test_catch_up_endpoint_converts_ai_name_error_to_information_request(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    make_thread(db, "failed", "+3002")
    add_message(db, "failed-customer", "failed", "customer", now)
    db.add(ThreadEvent(
        id="failed-missed-event",
        thread_id="failed",
        type="ai-reply-missed",
        agent_id=None,
        meta='{"message_id":"failed-customer","reason":"global-ai-off"}',
        at=now,
    ))
    db.commit()

    def fail_with_name_error(*_args, **_kwargs):
        raise NameError("name 'assistant_reply' is not defined")

    monkeypatch.setattr(main, "run_sms_reply_logic", fail_with_name_error)
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)

    result = catch_up_missed_messages(db)

    assert result == {
        "processed": True,
        "threadId": "failed",
        "outcome": "information-request",
        "remaining": 0,
    }
    assert db.get(Thread, "failed").state == "needs-review"
    event = db.query(ThreadEvent).filter_by(thread_id="failed", type="information-request").one()
    assert json.loads(event.meta) == {
        "reason": "Catch-up failed: NameError",
        "status": "pending",
        "customer_message_id": "failed-customer",
    }
    db.close()


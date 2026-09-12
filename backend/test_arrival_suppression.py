import json
from datetime import datetime, timedelta

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, ReplyInput, Thread, ThreadEvent, WebhookSMSInput


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_thread(db, thread_id="arrival-suppression", account="primary"):
    now = datetime.utcnow()
    thread = Thread(
        id=thread_id,
        customer_phone="+61412345678",
        sms_account_key=account,
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.commit()
    return thread


def outbound_messages(db, thread_id):
    return db.query(Message).filter(
        Message.thread_id == thread_id,
        Message.role.in_(["agent", "system", "draft"]),
    ).all()


@pytest.mark.parametrize(
    ("account", "destination", "text"),
    [
        ("primary", "61400000010", "Here"),
        ("secondary", "61420136756", "At the door - should I knock?"),
    ],
)
def test_webhook_arrivals_are_suppressed_before_every_responder_selection(
    monkeypatch, account, destination, text,
):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)
    monkeypatch.setattr(main.mobilemessage_service, "load_accounts_config", lambda: {
        "primary": {"sender": "61400000010", "enabled": True},
        "secondary": {"sender": "61420136756", "enabled": True},
    })
    monkeypatch.setattr(
        main,
        "load_first_contact_autoresponder",
        lambda *_args: pytest.fail("fixed responder selection must occur after arrival classification"),
    )
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: pytest.fail("arrival turn must never dispatch SMS"),
    )

    result = main.webhook_sms(WebhookSMSInput.model_validate({
        "sender": "0412 345 678",
        "to": destination,
        "message": text,
        "message_id": f"arrival-{account}",
        "received_at": "2026-09-12 10:00:00",
    }), BackgroundTasks(), db)

    assert result["arrival_suppressed"] is True
    thread = db.get(Thread, result["thread_id"])
    assert thread.sms_account_key == account
    assert outbound_messages(db, thread.id) == []
    events = db.query(ThreadEvent).filter_by(
        thread_id=thread.id, type=main.ARRIVAL_SUPPRESSION_EVENT_TYPE,
    ).all()
    assert len(events) == 1
    assert json.loads(events[0].meta)["sms_account_key"] == account
    db.close()


def test_fragmented_arrival_with_follow_up_suppresses_ai_delayed_and_retry_paths(monkeypatch):
    db = make_db()
    thread = add_thread(db)
    now = datetime.utcnow()
    fragments = [
        Message(
            id="arrival-fragment-one", thread_id=thread.id, role="customer",
            text="Hello", provider_message_id="fragment-provider-one", at=now,
        ),
        Message(
            id="arrival-fragment-two", thread_id=thread.id, role="customer",
            text="outside, which door should I use?", provider_message_id="fragment-provider-two",
            at=now + timedelta(seconds=1),
        ),
    ]
    db.add_all(fragments)
    db.commit()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "openai_client", pytest.fail)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: pytest.fail("arrival retry must never dispatch SMS"),
    )

    for _ in range(2):
        assert main.run_sms_reply_with_catch_up(
            db, thread.id, fragments[-1].text, fragments[-1].provider_message_id,
            fragments[-1].at, dispatch_sms=True,
        ) == (False, False)

    assert outbound_messages(db, thread.id) == []
    event = db.query(ThreadEvent).filter_by(
        thread_id=thread.id, type=main.ARRIVAL_SUPPRESSION_EVENT_TYPE,
    ).one()
    assert json.loads(event.meta)["fragment_message_ids"] == [
        "arrival-fragment-one", "arrival-fragment-two",
    ]
    assert db.query(ThreadEvent).filter_by(
        thread_id=thread.id, type=main.ARRIVAL_SUPPRESSION_EVENT_TYPE,
    ).count() == 1
    db.close()


def test_arrival_blocks_fixed_response_manual_reply_and_existing_draft_send(monkeypatch):
    db = make_db()
    thread = add_thread(db, "all-outbound-paths")
    now = datetime.utcnow()
    arrival = Message(
        id="all-paths-arrival", thread_id=thread.id, role="customer",
        text="Arrived, can you let me in?", at=now,
    )
    draft = Message(
        id="stale-draft", thread_id=thread.id, role="draft",
        text="A stale queued reply", at=now + timedelta(seconds=1),
    )
    db.add_all([arrival, draft])
    db.commit()
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: pytest.fail("no customer-facing path may dispatch"),
    )

    main.send_first_contact_auto_reply(
        db, thread, arrival, {"message": "Fixed hello", "cooldownDays": 30}, True,
    )
    with pytest.raises(HTTPException) as manual_error:
        main.reply_thread(thread.id, ReplyInput(agentId="tester", text="Manual reply"), db)
    with pytest.raises(HTTPException) as approval_error:
        main.approve_draft_message(draft.id, db)

    assert manual_error.value.status_code == 409
    assert approval_error.value.status_code == 409
    assert db.get(Message, draft.id).role == "draft"
    assert len(outbound_messages(db, thread.id)) == 1  # only the pre-existing stale draft
    assert db.query(ThreadEvent).filter_by(
        thread_id=thread.id, type=main.ARRIVAL_SUPPRESSION_EVENT_TYPE,
    ).count() == 1
    db.close()


def test_newest_non_arrival_turn_remains_eligible_after_historical_arrival(monkeypatch):
    db = make_db()
    thread = add_thread(db, "historical-arrival-control")
    now = datetime.utcnow()
    db.add_all([
        Message(id="old-arrival", thread_id=thread.id, role="customer", text="I'm here", at=now),
        Message(id="old-response", thread_id=thread.id, role="system", text="Seen", at=now + timedelta(seconds=1)),
        Message(
            id="normal-new-turn", thread_id=thread.id, role="customer",
            text="What times are available tomorrow?", at=now + timedelta(seconds=2),
        ),
    ])
    db.commit()

    assert main.suppress_arrival_customer_turn(db, thread) is False
    sent = []
    monkeypatch.setattr(main.mobilemessage_service, "send_sms", lambda *args, **kwargs: sent.append(args) or {})
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)
    main.send_first_contact_auto_reply(
        db, thread, db.get(Message, "normal-new-turn"),
        {"message": "Existing normal handling", "cooldownDays": 30}, True,
    )

    assert len(sent) == 1
    assert any(message.text == "Existing normal handling" for message in outbound_messages(db, thread.id))
    db.close()

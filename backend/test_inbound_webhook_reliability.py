import asyncio
from datetime import datetime

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import (
    Base,
    InboundWebhookReceipt,
    Message,
    WebhookSMSInput,
    inbound_webhook_identity,
    webhook_sms,
)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def receive(db, raw_payload):
    return webhook_sms(
        WebhookSMSInput.model_validate(raw_payload),
        BackgroundTasks(),
        db,
    )


def test_original_outbound_id_does_not_collapse_separate_inbound_messages(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", False)
    common = {
        "sender": "0412 345 678",
        "to": "61400000010",
        "type": "inbound",
        "original_message_id": "same-outbound-message",
    }

    first = receive(db, {
        **common,
        "message": "First reply",
        "received_at": "2026-08-11 10:00:00",
    })
    second_payload = {
        **common,
        "message": "Second reply",
        "received_at": "2026-08-11 10:00:01",
    }
    second = receive(db, second_payload)
    retry = receive(db, second_payload)

    messages = db.query(Message).filter(Message.role == "customer").order_by(Message.at).all()
    assert first.get("duplicate") is None
    assert second.get("duplicate") is None
    assert retry["duplicate"] is True
    assert [message.text for message in messages] == ["First reply", "Second reply"]
    assert messages[0].provider_message_id.startswith("inbound:")
    assert messages[0].provider_message_id != messages[1].provider_message_id
    assert db.query(InboundWebhookReceipt).count() == 2
    db.close()


def test_same_customer_on_two_inbound_numbers_creates_separate_threads(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", False)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {
            "primary": {"sender": "61400000010", "enabled": True},
            "secondary": {"sender": "61420136756", "enabled": True},
        },
    )

    primary = receive(db, {
        "sender": "0412 345 678",
        "to": "61400000010",
        "message": "Primary line",
        "message_id": "same-provider-id",
        "received_at": "2026-08-11 10:00:00",
    })
    secondary = receive(db, {
        "sender": "0412 345 678",
        "to": "+61 420 136 756",
        "message": "Secondary line",
        "message_id": "same-provider-id",
        "received_at": "2026-08-11 10:00:01",
    })

    threads = db.query(main.Thread).order_by(main.Thread.sms_account_key).all()
    assert primary["thread_id"] != secondary["thread_id"]
    assert [(thread.sms_account_key, thread.customer_phone) for thread in threads] == [
        ("primary", "+61412345678"),
        ("secondary", "+61412345678"),
    ]
    assert db.query(Message).filter(Message.role == "customer").count() == 2
    db.close()


def test_first_contact_greeting_is_selected_by_inbound_sms_account(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {
            "primary": {"sender": "61400000010", "enabled": True},
            "secondary": {"sender": "61420136756", "enabled": True},
        },
    )
    configs = {
        "primary": {"enabled": True, "cooldownDays": 30, "delaySeconds": 5, "message": "Tori hello"},
        "secondary": {"enabled": True, "cooldownDays": 7, "delaySeconds": 20, "message": "Anonymous hello"},
    }
    selected = []

    def account_config(key="primary"):
        selected.append(key)
        return configs[key]

    monkeypatch.setattr(main, "load_first_contact_autoresponder", account_config)

    primary_tasks = BackgroundTasks()
    primary = webhook_sms(WebhookSMSInput.model_validate({
        "sender": "0412 345 678",
        "to": "61400000010",
        "message": "Hello Tori",
        "received_at": "2026-08-11 10:00:00",
    }), primary_tasks, db)
    secondary_tasks = BackgroundTasks()
    secondary = webhook_sms(WebhookSMSInput.model_validate({
        "sender": "0412 345 678",
        "to": "61420136756",
        "message": "Hello Anonymous",
        "received_at": "2026-08-11 10:00:01",
    }), secondary_tasks, db)

    assert selected == ["primary", "secondary"]
    assert primary["first_contact_delay_seconds"] == 5
    assert secondary["first_contact_delay_seconds"] == 20
    assert primary_tasks.tasks[0].args[2]["message"] == "Tori hello"
    assert secondary_tasks.tasks[0].args[2]["message"] == "Anonymous hello"
    db.close()


def test_secondary_line_is_anonymous_autoresponder_only(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {
            "primary": {"sender": "61400000010", "enabled": True},
            "secondary": {"sender": "61420136756", "enabled": True},
        },
    )
    monkeypatch.setattr(
        main,
        "load_first_contact_autoresponder",
        lambda _key="primary": {
            "enabled": False,
            "cooldownDays": 1,
            "delaySeconds": 0,
            "message": "",
        },
    )
    ai_calls = []

    def fake_ai(db, thread_id, body, provider_message_id, received_at, **kwargs):
        thread = db.query(main.Thread).filter(main.Thread.id == thread_id).one()
        ai_calls.append((thread.sms_account_key, body))
        return False, False

    monkeypatch.setattr(main, "run_sms_reply_logic", fake_ai)

    secondary = receive(db, {
        "sender": "0412 345 678",
        "to": "61420136756",
        "message": "Who is this?",
        "message_id": "secondary-no-tori",
        "received_at": "2026-08-13 10:00:00",
    })
    primary = receive(db, {
        "sender": "0412 345 679",
        "to": "61400000010",
        "message": "Hello Tori",
        "message_id": "primary-tori",
        "received_at": "2026-08-13 10:00:01",
    })

    assert secondary["autoresponder_only"] is True
    assert primary.get("autoresponder_only") is None
    assert ai_calls == [("primary", "Hello Tori")]
    skipped = db.query(main.ThreadEvent).filter(
        main.ThreadEvent.thread_id == secondary["thread_id"],
        main.ThreadEvent.type == "ai-reply-skipped",
    ).one()
    assert "account-autoresponder-only" in skipped.meta
    db.close()


def test_real_inbound_message_id_still_deduplicates_retries(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", False)
    payload = {
        "sender": "0412 345 678",
        "message": "Hello",
        "message_id": "actual-inbound-id",
        "original_message_id": "outbound-correlation-only",
        "received_at": "2026-08-11 10:00:00",
    }

    receive(db, payload)
    retry = receive(db, payload)

    assert retry["duplicate"] is True
    assert db.query(Message).filter(Message.role == "customer").count() == 1
    assert db.query(Message).filter(Message.role == "customer").one().provider_message_id == "actual-inbound-id"
    db.close()


def test_exact_retry_of_pre_fix_original_id_record_is_not_reinserted(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", False)
    payload = {
        "sender": "0412 345 678",
        "message": "Already stored",
        "original_message_id": "legacy-outbound-id",
        "received_at": "2026-08-11 10:00:00",
    }
    first = WebhookSMSInput.model_validate(payload)
    # Reproduce how the old handler stored original_message_id as the inbound ID.
    first.providerMessageId = first.originalMessageId
    webhook_sms(first, BackgroundTasks(), db)

    retry = receive(db, payload)

    assert retry["duplicate"] is True
    assert db.query(Message).filter(Message.role == "customer").count() == 1
    assert db.query(InboundWebhookReceipt).count() == 1
    db.close()


def test_identity_uses_original_message_id_only_as_part_of_fingerprint():
    payload = WebhookSMSInput.model_validate({
        "sender": "0412 345 678",
        "message": "Hello",
        "original_message_id": "outbound-only",
        "received_at": "2026-08-11 10:00:00",
    })

    identity, is_explicit = inbound_webhook_identity(
        payload,
        "+61412345678",
        datetime(2026, 8, 11, 10, 0, 0),
    )

    assert payload.providerMessageId is None
    assert payload.originalMessageId == "outbound-only"
    assert is_explicit is False
    assert identity.startswith("inbound:")


def test_typing_delay_yields_without_occupying_request_worker(monkeypatch):
    calls = []

    async def fake_sleep(seconds):
        calls.append(("sleep", seconds))

    async def fake_to_thread(function, *args):
        calls.append(("to_thread", function.__name__, args))

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(main.random if hasattr(main, "random") else __import__("random"), "randint", lambda *_args: 30)

    asyncio.run(main.process_sms_reply_delayed(
        "thread-id",
        "message",
        "provider-id",
        datetime(2026, 8, 11, 10, 0, 0),
    ))

    assert calls[0] == ("sleep", 30)
    assert calls[1][0:2] == ("to_thread", "_process_sms_reply")

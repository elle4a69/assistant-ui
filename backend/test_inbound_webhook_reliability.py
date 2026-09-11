import asyncio
from datetime import datetime

import pytest
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
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {"primary": {"sender": "61400000010", "enabled": True}},
    )
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


def test_first_contact_fixed_responder_bypasses_ai_training_approval(monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    thread = main.Thread(
        id="training-first-contact",
        customer_phone="+61412345678",
        sms_account_key="secondary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=1,
        auto_reply_enabled=True,
        created_at=now,
        updated_at=now,
    )
    customer = Message(
        id="training-first-contact-inbound",
        thread_id=thread.id,
        role="customer",
        text="Hi",
        provider_message_id="provider-first-contact",
        at=now,
    )
    db.add_all([thread, customer])
    db.commit()

    sent = []
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", True)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda phone, text, **kwargs: sent.append((phone, text, kwargs)) or {"status": "success"},
    )
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)

    main.send_first_contact_auto_reply(
        db,
        thread,
        customer,
        {"message": "Fixed welcome", "cooldownDays": 30},
        dispatch_sms=True,
    )

    outbound = db.query(Message).filter(Message.thread_id == thread.id, Message.role == "system").one()
    assert outbound.text == "Fixed welcome"
    assert thread.state == "auto-reply"
    assert sent == [(
        "+61412345678",
        "Fixed welcome",
        {"idempotency_key": outbound.id, "account_key": "secondary"},
    )]
    event = db.query(main.ThreadEvent).filter(main.ThreadEvent.thread_id == thread.id).one()
    assert event.type == "auto-reply-sent"
    assert '"source": "first-contact-auto-responder"' in event.meta
    db.close()

def test_both_account_scoped_lines_can_use_conversational_ai(monkeypatch):
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

    assert secondary.get("autoresponder_only") is None
    assert primary.get("autoresponder_only") is None
    assert ai_calls == [
        ("secondary", "Who is this?"),
        ("primary", "Hello Tori"),
    ]
    assert db.query(main.ThreadEvent).filter(
        main.ThreadEvent.thread_id == secondary["thread_id"],
        main.ThreadEvent.type == "ai-reply-skipped",
    ).count() == 0
    assert main.account_allows_conversational_ai("primary") is True
    assert main.account_allows_conversational_ai("secondary") is True
    assert main.account_allows_conversational_ai("unknown") is False
    db.close()


def test_unknown_supplied_inbound_number_is_rejected_not_routed_to_tori(monkeypatch):
    db = make_db()
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {
            "primary": {"sender": "61400000010", "enabled": True},
            "secondary": {"sender": "61420136756", "enabled": True},
        },
    )

    with pytest.raises(main.HTTPException) as exc_info:
        receive(db, {
            "sender": "0412 345 678",
            "to": "61499999999",
            "message": "Do not route this to Tori",
            "message_id": "unknown-destination",
            "received_at": "2026-08-13 10:00:00",
        })

    assert exc_info.value.status_code == 422
    assert db.query(main.Thread).count() == 0
    db.close()


@pytest.mark.parametrize("destination", [None, "", "   ", "61499999999"])
def test_unproven_inbound_destinations_fail_closed_without_processing(monkeypatch, destination):
    db = make_db()
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {
            "primary": {"sender": "61400000010", "enabled": True},
            "secondary": {"sender": "61420136756", "enabled": True},
        },
    )
    processed = []
    monkeypatch.setattr(main, "process_inbound_sms", lambda *args: processed.append(args))
    payload = {
        "sender": "0412 345 678",
        "message": "This must not be routed.",
        "message_id": "unproven-destination",
        "received_at": "2026-08-13 10:00:00",
    }
    if destination is not None:
        payload["to"] = destination

    with pytest.raises(main.HTTPException) as exc_info:
        receive(db, payload)

    assert exc_info.value.status_code == 422
    assert processed == []
    assert db.query(main.Thread).count() == 0
    db.close()


@pytest.mark.parametrize(
    "accounts,destination",
    [
        (
            {
                "primary": {"sender": "04 0000 0010", "enabled": True},
                "secondary": {"sender": "+61 400 000 010", "enabled": True},
            },
            "61400000010",
        ),
        (
            {
                "primary": {"sender": "61400000010", "enabled": True},
                "secondary": {"sender": "61400000010", "enabled": True},
            },
            "61400000010",
        ),
    ],
)
def test_ambiguous_inbound_destination_fails_closed_without_processing_or_writes(monkeypatch, accounts, destination):
    db = make_db()
    monkeypatch.setattr(main.mobilemessage_service, "load_accounts_config", lambda: accounts)
    processed = []
    monkeypatch.setattr(main, "process_inbound_sms", lambda *args: processed.append(args))

    with pytest.raises(main.HTTPException) as exc_info:
        receive(db, {
            "sender": "0412 345 678",
            "to": destination,
            "message": "This must not be routed.",
            "message_id": "ambiguous-destination",
            "received_at": "2026-08-13 10:00:00",
        })

    assert exc_info.value.status_code == 422
    assert processed == []
    assert db.query(main.Thread).count() == 0
    assert db.query(Message).count() == 0
    assert db.query(InboundWebhookReceipt).count() == 0
    db.close()


@pytest.mark.parametrize(
    ("destination", "expected_account"),
    [
        ("61400000010", "primary"),
        ("+61 420 136 756", "secondary"),
    ],
)
def test_valid_inbound_destinations_pass_only_the_proven_account(monkeypatch, destination, expected_account):
    db = make_db()
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {
            "primary": {"sender": "61400000010", "enabled": True},
            "secondary": {"sender": "61420136756", "enabled": True},
        },
    )
    processed = []
    monkeypatch.setattr(main, "process_inbound_sms", lambda *args: processed.append(args) or {"status": "success"})

    result = receive(db, {
        "sender": "0412 345 678",
        "to": destination,
        "message": "Correctly routed.",
        "message_id": f"valid-{expected_account}",
        "received_at": "2026-08-13 10:00:00",
    })

    assert result == {"status": "success"}
    assert len(processed) == 1
    assert processed[0][-1] == expected_account
    db.close()


def test_real_inbound_message_id_still_deduplicates_retries(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", False)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {"primary": {"sender": "61400000010", "enabled": True}},
    )
    payload = {
        "sender": "0412 345 678",
        "to": "61400000010",
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
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_accounts_config",
        lambda: {"primary": {"sender": "61400000010", "enabled": True}},
    )
    payload = {
        "sender": "0412 345 678",
        "to": "61400000010",
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


def test_superseded_customer_fragment_cannot_generate_a_reply(monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    thread = main.Thread(
        id="burst-thread",
        customer_phone="+61412345678",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=2,
    )
    db.add(thread)
    db.add_all([
        Message(
            id="old-fragment",
            thread_id=thread.id,
            role="customer",
            text="Can I book tomorrow",
            provider_message_id="old-provider",
            at=now,
        ),
        Message(
            id="new-fragment",
            thread_id=thread.id,
            role="customer",
            text="At 3pm please",
            provider_message_id="new-provider",
            at=now + main.timedelta(milliseconds=1),
        ),
    ])
    db.commit()

    result = main.run_sms_reply_logic(
        db, thread.id, "Can I book tomorrow", "old-provider", now, dispatch_sms=False
    )

    assert result == (False, False)
    assert db.query(Message).filter(
        Message.thread_id == thread.id,
        Message.role.in_(["system", "draft"]),
    ).count() == 0
    cancelled = db.query(main.ThreadEvent).filter(main.ThreadEvent.type == "ai-reply-cancelled").one()
    assert "superseded-by-newer-customer-message" in cancelled.meta
    db.close()


def test_model_input_consolidates_latest_customer_burst_with_deep_history():
    now = datetime.utcnow()
    history = []
    for index in range(15):
        history.append(type("Stored", (), {
            "role": "customer" if index % 2 == 0 else "system",
            "text": f"historical-{index}",
        })())
    history.extend([
        type("Stored", (), {"role": "customer", "text": "Tomorrow"})(),
        type("Stored", (), {"role": "customer", "text": "At 3pm"})(),
    ])

    model_input = main.build_model_input(
        history,
        current_history_text="At 3pm",
        enriched_current_prompt="Combined customer turn:\nTomorrow\nAt 3pm",
    )

    assert any(item["content"] == "historical-0" for item in model_input)
    assert model_input[-1] == {
        "role": "user",
        "content": "Combined customer turn:\nTomorrow\nAt 3pm",
    }
    assert not any(item["content"] == "Tomorrow" for item in model_input)


def test_reply_pipeline_uses_chronological_thread_and_excludes_other_sms_account(monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    primary = main.Thread(
        id="primary-context-thread",
        customer_phone="+61412345678",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=2,
    )
    secondary = main.Thread(
        id="secondary-context-thread",
        customer_phone=primary.customer_phone,
        sms_account_key="secondary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=1,
    )
    db.add_all([primary, secondary])
    db.add_all([
        Message(
            id="primary-earlier-customer",
            thread_id=primary.id,
            role="customer",
            text="Earlier primary-line context",
            at=now,
        ),
        Message(
            id="primary-earlier-agent",
            thread_id=primary.id,
            role="system",
            text="Earlier primary-line reply",
            at=now + main.timedelta(seconds=1),
        ),
        Message(
            id="primary-fragment-one",
            thread_id=primary.id,
            role="customer",
            text="First half of newest turn",
            provider_message_id="primary-fragment-one-provider",
            at=now + main.timedelta(seconds=2),
        ),
        Message(
            id="primary-fragment-two",
            thread_id=primary.id,
            role="customer",
            text="Second half of newest turn",
            provider_message_id="primary-fragment-two-provider",
            at=now + main.timedelta(seconds=3),
        ),
        Message(
            id="secondary-private-context",
            thread_id=secondary.id,
            role="customer",
            text="SECONDARY ACCOUNT MUST NOT LEAK",
            at=now + main.timedelta(seconds=1),
        ),
    ])
    db.commit()

    class CapturingResponses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return type("Response", (), {"output": [], "output_text": "Got it."})()

    responses = CapturingResponses()
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_authority_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])

    result = main.run_sms_reply_logic(
        db,
        primary.id,
        "Second half of newest turn",
        "primary-fragment-two-provider",
        now + main.timedelta(seconds=3),
        dispatch_sms=False,
    )

    assert result == (False, False)
    model_input = responses.calls[0]["input"]
    assert "Earlier primary-line context" in model_input[0]["content"]
    assert "Earlier primary-line reply" in model_input[1]["content"]
    assert "First half of newest turn" in model_input[-1]["content"]
    assert "Second half of newest turn" in model_input[-1]["content"]
    assert not any(
        item["content"].strip() == "First half of newest turn"
        for item in model_input
    )
    assert not any(
        "SECONDARY ACCOUNT MUST NOT LEAK" in item["content"]
        for item in model_input
    )
    db.close()


def test_superseded_reply_catches_up_newest_combined_turn_once(monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    thread = main.Thread(
        id="catch-up-race-thread",
        customer_phone="+61412345001",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=1,
    )
    old_message = Message(
        id="old-turn",
        thread_id=thread.id,
        role="customer",
        text="Tell me more",
        provider_message_id="old-provider",
        at=now,
    )
    db.add_all([thread, old_message])
    db.commit()

    class RacingResponses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                db.add_all([
                    Message(
                        id="new-fragment-one",
                        thread_id=thread.id,
                        role="customer",
                        text="One more thing",
                        provider_message_id="new-provider-one",
                        at=now + main.timedelta(seconds=1),
                    ),
                    Message(
                        id="new-fragment-two",
                        thread_id=thread.id,
                        role="customer",
                        text="What should I bring?",
                        provider_message_id="new-provider-two",
                        at=now + main.timedelta(seconds=2),
                    ),
                ])
                db.commit()
                return type("Response", (), {"output": [], "output_text": "Stale reply"})()
            return type("Response", (), {"output": [], "output_text": "Newest reply"})()

    responses = RacingResponses()
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_authority_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])

    main.run_sms_reply_with_catch_up(
        db, thread.id, old_message.text, old_message.provider_message_id, old_message.at,
        dispatch_sms=False,
    )
    # Simulate the independently queued job for the newest fragment arriving later.
    main.run_sms_reply_with_catch_up(
        db, thread.id, "What should I bring?", "new-provider-two",
        now + main.timedelta(seconds=2), dispatch_sms=False,
    )

    assert len(responses.calls) == 2
    newest_input = responses.calls[1]["input"][-1]["content"]
    assert "One more thing" in newest_input
    assert "What should I bring?" in newest_input
    replies = db.query(Message).filter(
        Message.thread_id == thread.id,
        Message.role == "system",
    ).all()
    assert [reply.text for reply in replies] == ["Newest reply"]
    cancelled = db.query(main.ThreadEvent).filter_by(
        thread_id=thread.id, type="ai-reply-cancelled",
    ).one()
    assert "newer-customer-message-during-generation" in cancelled.meta
    db.close()


def test_superseded_reply_does_not_override_human_answer_to_newest_turn(monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    thread = main.Thread(
        id="human-wins-race-thread",
        customer_phone="+61412345002",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=1,
    )
    old_message = Message(
        id="human-race-old-turn",
        thread_id=thread.id,
        role="customer",
        text="Old question",
        provider_message_id="human-race-old-provider",
        at=now,
    )
    db.add_all([thread, old_message])
    db.commit()

    class HumanReplyRaceResponses:
        calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            db.add_all([
                Message(
                    id="human-race-new-turn",
                    thread_id=thread.id,
                    role="customer",
                    text="Newest question",
                    provider_message_id="human-race-new-provider",
                    at=now + main.timedelta(seconds=1),
                ),
                Message(
                    id="human-race-agent-answer",
                    thread_id=thread.id,
                    role="agent",
                    text="Human answer",
                    provider_message_id="manual-reply:human-race",
                    at=now + main.timedelta(seconds=2),
                ),
            ])
            db.commit()
            return type("Response", (), {"output": [], "output_text": "Stale AI answer"})()

    responses = HumanReplyRaceResponses()
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_authority_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])

    main.run_sms_reply_with_catch_up(
        db, thread.id, old_message.text, old_message.provider_message_id, old_message.at,
        dispatch_sms=False,
    )
    main.run_sms_reply_with_catch_up(
        db, thread.id, "Newest question", "human-race-new-provider",
        now + main.timedelta(seconds=1), dispatch_sms=False,
    )

    assert responses.calls == 1
    assert db.query(Message).filter(
        Message.thread_id == thread.id,
        Message.role == "system",
    ).count() == 0
    assert db.query(Message).filter_by(
        thread_id=thread.id, role="agent", text="Human answer",
    ).count() == 1
    db.close()

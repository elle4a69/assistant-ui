from datetime import datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import (
    Base,
    ContactBlockInput,
    Message,
    Thread,
    WebhookSMSInput,
    get_thread_detail,
    get_threads,
    list_catch_up_candidates,
    set_contact_blocked,
    webhook_sms,
)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_thread(db, thread_id: str, account_key: str, blocked: bool = False) -> Thread:
    now = datetime.utcnow()
    thread = Thread(
        id=thread_id,
        customer_phone="+61412345678",
        sms_account_key=account_key,
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        auto_reply_enabled=True,
        contact_blocked=blocked,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.commit()
    return thread


def test_contact_block_api_persists_only_the_selected_account_thread():
    db = make_db()
    primary = add_thread(db, "primary-thread", "primary")
    secondary = add_thread(db, "secondary-thread", "secondary")

    result = set_contact_blocked(primary.id, ContactBlockInput(blocked=True), db)

    db.refresh(primary)
    db.refresh(secondary)
    assert result == {"status": "success", "contactBlocked": True}
    assert primary.contact_blocked is True
    assert primary.auto_reply_enabled is True
    assert secondary.contact_blocked is False
    assert get_thread_detail(primary.id, db)["contactBlocked"] is True
    listed = {item["id"]: item for item in get_threads(None, None, None, None, db)}
    assert listed[primary.id]["contactBlocked"] is True
    assert listed[secondary.id]["contactBlocked"] is False

    reversed_result = set_contact_blocked(primary.id, ContactBlockInput(blocked=False), db)
    assert reversed_result["contactBlocked"] is False
    assert [event.type for event in primary.events][-2:] == ["contact-blocked", "contact-unblocked"]
    db.close()


def test_existing_thread_table_adds_unblocked_contact_state(monkeypatch, tmp_path):
    database_path = tmp_path / "legacy-threads.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    now = datetime.utcnow()
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE threads (
                id VARCHAR NOT NULL PRIMARY KEY,
                customer_phone VARCHAR NOT NULL,
                sms_account_key VARCHAR NOT NULL DEFAULT 'primary',
                state VARCHAR NOT NULL DEFAULT 'auto-reply',
                priority VARCHAR NOT NULL DEFAULT 'medium',
                assigned_agent_id VARCHAR,
                sla_due_at DATETIME NOT NULL,
                unread_count INTEGER NOT NULL DEFAULT 0,
                auto_reply_enabled BOOLEAN NOT NULL DEFAULT 1,
                pending_slots TEXT,
                pending_booking TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE (sms_account_key, customer_phone)
            )
        """)
        connection.exec_driver_sql(
            """
            INSERT INTO threads (
                id, customer_phone, sms_account_key, state, priority, sla_due_at,
                unread_count, auto_reply_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-thread", "+61412345678", "primary", "auto-reply", "medium",
                now + timedelta(hours=1), 0, True, now, now,
            ),
        )

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(
        main,
        "SessionLocal",
        sessionmaker(autocommit=False, autoflush=False, bind=engine),
    )
    main.init_db()

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(threads)").fetchall()
        }
        assert "contact_blocked" in columns
        assert connection.exec_driver_sql(
            "SELECT contact_blocked FROM threads WHERE id = 'legacy-thread'"
        ).scalar_one() == 0


def test_blocked_contact_is_stored_but_skips_account_scoped_automation(monkeypatch):
    db = make_db()
    primary = add_thread(db, "primary-thread", "primary", blocked=True)
    add_thread(db, "secondary-thread", "secondary")
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
            "cooldownDays": 30,
            "delaySeconds": 0,
            "message": "",
        },
    )
    ai_calls = []
    monkeypatch.setattr(
        main,
        "run_sms_reply_logic",
        lambda _db, thread_id, *_args, **_kwargs: ai_calls.append(thread_id) or (False, False),
    )

    blocked_tasks = BackgroundTasks()
    blocked_result = webhook_sms(WebhookSMSInput.model_validate({
        "sender": "0412 345 678",
        "to": "61400000010",
        "message": "I'm here",
        "message_id": "blocked-primary-message",
        "received_at": "2026-08-31 10:00:00",
    }), blocked_tasks, db)
    allowed_result = webhook_sms(WebhookSMSInput.model_validate({
        "sender": "0412 345 678",
        "to": "61420136756",
        "message": "Hello",
        "message_id": "allowed-secondary-message",
        "received_at": "2026-08-31 10:00:01",
    }), BackgroundTasks(), db)

    assert blocked_result["contact_blocked"] is True
    assert blocked_tasks.tasks == []
    assert ai_calls == [allowed_result["thread_id"]]
    assert db.query(Message).filter(
        Message.thread_id == primary.id,
        Message.role == "customer",
    ).one().text == "I'm here"
    event_types = [event.type for event in primary.events]
    assert "automated-handling-skipped" in event_types
    assert "customer-arrived" not in event_types
    assert primary.id not in {
        candidate_thread.id for candidate_thread, _message in list_catch_up_candidates(db)
    }
    db.close()


def test_reply_logic_defensively_stops_for_a_blocked_contact(monkeypatch):
    db = make_db()
    thread = add_thread(db, "blocked-thread", "primary", blocked=True)
    monkeypatch.setattr(
        main,
        "account_allows_conversational_ai",
        lambda *_args: (_ for _ in ()).throw(AssertionError("blocked contact reached AI routing")),
    )

    assert main.run_sms_reply_logic(
        db,
        thread.id,
        "Hello",
        "provider-message",
        datetime.utcnow(),
        dispatch_sms=False,
    ) == (False, False)
    db.close()

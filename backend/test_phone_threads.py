from datetime import datetime, timedelta

import main
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import (
    ArrivalSession,
    Base,
    BlockedContact,
    CalendarEvent,
    Message,
    Thread,
    canonical_phone_number,
    find_thread_by_phone,
    get_thread_detail,
    get_threads,
    is_contact_blocked,
    list_blocked_contacts,
    list_catch_up_candidates,
    set_thread_blocked,
    set_thread_pinned,
    unblock_contact,
    ThreadBlockedInput,
    ThreadPinnedInput,
    WebhookSMSInput,
    process_inbound_sms,
)


def test_australian_mobile_formats_share_one_canonical_value():
    expected = "+61412345678"
    assert canonical_phone_number("0412 345 678") == expected
    assert canonical_phone_number("61412345678") == expected
    assert canonical_phone_number("+61 412 345 678") == expected
    assert canonical_phone_number("(04) 1234 5678") == expected
    assert canonical_phone_number("+61 0412 345 678") == expected
    assert canonical_phone_number("610412345678") == expected


def test_thread_lookup_prefers_established_conversation_over_confirmation_duplicate():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()

    conversation = Thread(
        id="conversation",
        customer_phone="0412345678",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    confirmation_only = Thread(
        id="confirmation-only",
        customer_phone="+61412345678",
        state="resolved",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add_all([conversation, confirmation_only])
    db.flush()
    db.add_all([
        Message(
            id="customer-message",
            thread_id=conversation.id,
            role="customer",
            text="Are you free today?",
            at=now,
        ),
        Message(
            id="agent-message",
            thread_id=conversation.id,
            role="agent",
            text="Yes, what time suits?",
            at=now,
        ),
        Message(
            id="confirmation-message",
            thread_id=confirmation_only.id,
            role="agent",
            text="Your booking is confirmed.",
            at=now,
        ),
    ])
    db.add(CalendarEvent(
        id="deduplicated-booking", summary="Booking", customer_phone="+61412345678",
        sms_account_key="primary", thread_id=confirmation_only.id,
        start_time=now, end_time=now + timedelta(hours=1), status="scheduled", notes="",
    ))
    db.add(ArrivalSession(
        id="deduplicated-arrival", booking_id="deduplicated-booking",
        thread_id=confirmation_only.id, sms_account_key="primary",
        invite_token_hash="deduplicated-invite", status="active",
        expires_at=now + timedelta(hours=2), activated_at=now,
        created_at=now, last_activity_at=now,
    ))
    db.commit()

    matched = find_thread_by_phone(db, "+61 412 345 678")

    assert matched is not None
    assert matched.id == conversation.id
    assert matched.customer_phone == "+61412345678"
    assert {message.text for message in matched.messages} == {
        "Are you free today?",
        "Yes, what time suits?",
        "Your booking is confirmed.",
    }
    assert db.query(Thread).count() == 1
    assert db.query(ArrivalSession).filter(ArrivalSession.id == "deduplicated-arrival").one().thread_id == conversation.id
    assert db.query(CalendarEvent).filter(CalendarEvent.id == "deduplicated-booking").one().thread_id == conversation.id
    db.close()


def test_thread_detail_orders_messages_by_received_time_then_id():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="ordered-thread",
        customer_phone="+61412345678",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.flush()
    db.add_all([
        Message(id="message-c", thread_id=thread.id, role="customer", text="Third", at=now + timedelta(seconds=2)),
        Message(id="message-b", thread_id=thread.id, role="agent", text="Second", at=now + timedelta(seconds=1)),
        Message(id="message-a", thread_id=thread.id, role="customer", text="First", at=now),
    ])
    db.commit()

    detail = get_thread_detail(thread.id, db)

    assert [message["text"] for message in detail["messages"]] == ["First", "Second", "Third"]
    db.close()


def test_thread_list_orders_conversations_by_latest_message_not_thread_update():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()

    stale_conversation = Thread(
        id="stale-conversation",
        customer_phone="+61411111111",
        state="taken-over",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now - timedelta(hours=2),
        updated_at=now,
    )
    recent_conversation = Thread(
        id="recent-conversation",
        customer_phone="+61422222222",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=30),
    )
    db.add_all([stale_conversation, recent_conversation])
    db.flush()
    db.add_all([
        Message(
            id="older-message",
            thread_id=stale_conversation.id,
            role="agent",
            text="Older message",
            at=now - timedelta(hours=1),
        ),
        Message(
            id="newest-message",
            thread_id=recent_conversation.id,
            role="customer",
            text="Newest message",
            at=now - timedelta(minutes=1),
        ),
    ])
    db.commit()

    items = get_threads(
        search=None,
        filterStatus=None,
        filterPriority=None,
        onlyUnread=None,
        db=db,
    )

    assert [item["id"] for item in items] == [recent_conversation.id, stale_conversation.id]
    assert items[0]["lastMessageText"] == "Newest message"
    db.close()


def test_thread_search_matches_message_body_not_just_phone_number():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="body-search-thread",
        customer_phone="+61412345678",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.flush()
    db.add_all([
        Message(
            id="old-matching-message",
            thread_id=thread.id,
            role="customer",
            text="Can you do a natural service?",
            at=now - timedelta(minutes=5),
        ),
        Message(
            id="latest-non-matching-message",
            thread_id=thread.id,
            role="agent",
            text="Yep, what day were you after?",
            at=now,
        ),
    ])
    db.commit()

    items = get_threads(
        search="natural service",
        filterStatus=None,
        filterPriority=None,
        onlyUnread=None,
        db=db,
    )

    assert [item["id"] for item in items] == [thread.id]
    db.close()


def test_pin_persists_and_pinned_threads_sort_above_newer_unpinned_threads():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    pinned = Thread(id="pinned", customer_phone="+61411111111", sms_account_key="primary",
                    state="auto-reply", priority="medium", sla_due_at=now + timedelta(hours=1),
                    unread_count=0, created_at=now, updated_at=now)
    newer = Thread(id="newer", customer_phone="+61422222222", sms_account_key="primary",
                   state="auto-reply", priority="medium", sla_due_at=now + timedelta(hours=1),
                   unread_count=0, created_at=now, updated_at=now)
    db.add_all([pinned, newer])
    db.flush()
    db.add_all([
        Message(id="older", thread_id=pinned.id, role="customer", text="older", at=now - timedelta(hours=1)),
        Message(id="newer-message", thread_id=newer.id, role="customer", text="newer", at=now),
    ])
    db.commit()

    assert set_thread_pinned(pinned.id, ThreadPinnedInput(pinned=True), db)["pinned"] is True
    db.expire_all()
    assert db.query(Thread).filter(Thread.id == pinned.id).one().pinned is True
    items = get_threads(search=None, filterStatus=None, filterPriority=None, onlyUnread=None, db=db)
    assert [item["id"] for item in items] == [pinned.id, newer.id]
    assert items[0]["pinned"] is True
    db.close()


def test_blocking_is_account_scoped_suppresses_catch_up_and_settings_can_unblock():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow() - timedelta(minutes=10)
    primary = Thread(id="primary-contact", customer_phone="+61412345678", sms_account_key="primary",
                     state="auto-reply", priority="medium", sla_due_at=now + timedelta(hours=1),
                     unread_count=1, created_at=now, updated_at=now)
    secondary = Thread(id="secondary-contact", customer_phone="+61412345678", sms_account_key="secondary",
                       state="auto-reply", priority="medium", sla_due_at=now + timedelta(hours=1),
                       unread_count=1, created_at=now, updated_at=now)
    db.add_all([primary, secondary])
    db.flush()
    db.add_all([
        Message(id="primary-inbound", thread_id=primary.id, role="customer", text="hello", at=now),
        Message(id="secondary-inbound", thread_id=secondary.id, role="customer", text="hello", at=now),
    ])
    db.commit()

    assert set_thread_blocked(primary.id, ThreadBlockedInput(blocked=True), db)["blocked"] is True
    assert is_contact_blocked(db, "primary", primary.customer_phone) is True
    assert is_contact_blocked(db, "secondary", secondary.customer_phone) is False
    assert [thread.id for thread, _ in list_catch_up_candidates(db)] == [secondary.id]

    blocked = list_blocked_contacts(db)
    assert [(item["smsAccountKey"], item["customerPhone"]) for item in blocked] == [
        ("primary", "+61412345678")
    ]
    unblock_contact("primary", "+61 412 345 678", db)
    assert db.query(BlockedContact).count() == 0
    assert {thread.id for thread, _ in list_catch_up_candidates(db)} == {primary.id, secondary.id}
    db.close()


def test_blocked_inbound_sms_is_stored_without_automated_handling(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    thread = Thread(id="blocked-inbound", customer_phone="+61412345678", sms_account_key="primary",
                    state="auto-reply", priority="medium", sla_due_at=now + timedelta(hours=1),
                    unread_count=0, created_at=now, updated_at=now)
    db.add_all([
        thread,
        BlockedContact(id="blocked-primary", sms_account_key="primary", customer_phone="+61412345678"),
    ])
    db.commit()
    automated_calls = []
    monkeypatch.setattr(main, "load_first_contact_autoresponder", lambda _key: {
        "enabled": True, "cooldownDays": 30, "delaySeconds": 0, "message": "Automatic greeting",
    })
    monkeypatch.setattr(main, "account_allows_conversational_ai", lambda _key: True)
    monkeypatch.setattr(main, "run_sms_reply_logic", lambda *args, **kwargs: automated_calls.append(args) or (False, False))

    result = process_inbound_sms(
        WebhookSMSInput.model_validate({
            "from": "+61 412 345 678", "body": "Please stop", "message_id": "blocked-inbound-1",
            "receivedAt": now.isoformat() + "Z", "isSimulation": True,
        }),
        BackgroundTasks(), db, "primary",
    )

    assert result["blocked"] is True
    assert automated_calls == []
    assert db.query(Message).filter(Message.thread_id == thread.id, Message.role == "customer").count() == 1
    skipped = db.query(main.ThreadEvent).filter(main.ThreadEvent.thread_id == thread.id).one()
    assert '"reason": "contact-blocked"' in skipped.meta
    db.close()

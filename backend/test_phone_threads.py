from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import (
    ArrivalSession,
    Base,
    CalendarEvent,
    Message,
    Thread,
    canonical_phone_number,
    find_thread_by_phone,
    get_thread_detail,
    get_threads,
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

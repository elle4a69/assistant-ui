import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import (
    Base,
    Message,
    Thread,
    ThreadEvent,
    get_threads,
    is_clear_customer_arrival,
    record_customer_arrival_event,
)


@pytest.mark.parametrize("message", [
    "I'm here",
    "We have arrived",
    "Just got here",
    "I'm at the front door",
    "Waiting outside",
    "I've pulled up out front",
])
def test_clear_arrival_phrases_are_detected(message):
    assert is_clear_customer_arrival(message) is True


@pytest.mark.parametrize("message", [
    "I'm not there yet",
    "I'm on my way",
    "I'm almost there",
    "I'll be there in five minutes",
    "When I arrive, where should I park?",
    "Have you arrived?",
    "Can you send the address?",
])
def test_future_negative_and_question_phrases_do_not_trigger(message):
    assert is_clear_customer_arrival(message) is False


def test_arrival_event_is_deduplicated_and_exposed_in_thread_list():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    db = sessionmaker(bind=test_engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="arrival-thread",
        customer_phone="+61412345678",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
        created_at=now,
        updated_at=now,
    )
    message = Message(
        id="arrival-message",
        thread_id=thread.id,
        role="customer",
        text="I'm here",
        at=now,
    )
    db.add_all([thread, message])
    db.flush()

    assert record_customer_arrival_event(db, thread, message.id, "clear-phrase") is True
    assert record_customer_arrival_event(db, thread, message.id, "ai") is False
    db.commit()

    arrival_events = db.query(ThreadEvent).filter(
        ThreadEvent.thread_id == thread.id,
        ThreadEvent.type == "customer-arrived",
    ).all()
    assert len(arrival_events) == 1
    assert json.loads(arrival_events[0].meta)["source_message_id"] == message.id

    item = get_threads(
        search=None,
        filterStatus=None,
        filterPriority=None,
        onlyUnread=None,
        db=db,
    )[0]
    assert item["lastArrivalEventId"] == arrival_events[0].id
    assert item["lastArrivalAt"] is not None
    db.close()

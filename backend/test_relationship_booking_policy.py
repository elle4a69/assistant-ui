from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main


@dataclass
class StoredMessage:
    role: str
    text: str


SERVICES = [
    {"id": "tori-session", "name": "Companion Session", "price": 300, "duration": 60},
    {"id": "anonymous", "name": "Anonymous Appointment", "price": 200, "duration": 30},
]


def history(*customer_turns: str):
    messages = []
    for index, text in enumerate(customer_turns):
        messages.append(StoredMessage("customer", text))
        if index < len(customer_turns) - 1:
            messages.append(StoredMessage("system", "Previous reply"))
    return messages


def test_dinner_and_personal_relationship_requests_are_declined_and_redirected(monkeypatch):
    monkeypatch.setattr(main, "load_booking_services", lambda: SERVICES)

    dinner = main.enforce_customer_conversation_policy(
        "primary", "Will you go to dinner with me?", "I'd love to", history("Will you go to dinner with me?"),
    )
    relationship = main.enforce_customer_conversation_policy(
        "secondary", "Can you be my girlfriend?", "Maybe babe", history("Can you be my girlfriend?"),
    )

    assert "don't do personal dates or relationships" in dinner
    assert "Companion Session" in dinner and "$300" in dinner
    assert "what day were you looking to book" in dinner.casefold()
    assert "don't do personal dates or relationships" in relationship
    assert "Anonymous Appointment" in relationship and "$200" in relationship
    assert "Companion Session" not in relationship


def test_first_social_reply_naturally_discloses_service_and_price(monkeypatch):
    monkeypatch.setattr(main, "load_booking_services", lambda: SERVICES)

    reply = main.enforce_customer_conversation_policy(
        "primary", "Hey gorgeous", "Hey, cheeky 😉", history("Hey gorgeous"),
    )

    assert reply.startswith("Hey, cheeky")
    assert "professional in-person appointment for $300" in reply
    assert reply.endswith("What day were you looking to book?")


def test_bounded_flirting_redirects_then_closes_social_loop(monkeypatch):
    monkeypatch.setattr(main, "load_booking_services", lambda: SERVICES)

    second = main.enforce_customer_conversation_policy(
        "primary",
        "You're so sexy babe",
        "You're making me blush 😉",
        history("Hey beautiful", "You're so sexy babe"),
    )
    third = main.enforce_customer_conversation_policy(
        "primary",
        "Talk to me babe",
        "What do you want to chat about?",
        history("Hey beautiful", "You're so sexy babe", "Talk to me babe"),
    )

    assert "making me blush" in second
    assert "$300" in second and "looking to book" in second
    assert "I'll leave the social chat there" in third
    assert "what do you want to chat" not in third.casefold()
    assert not third.endswith("?")


def test_account_prompts_and_prices_remain_isolated(monkeypatch):
    monkeypatch.setattr(main, "load_booking_services", lambda: SERVICES)

    primary_prompt = main.conversation_policy_instructions("primary", 1)
    secondary_prompt = main.conversation_policy_instructions("secondary", 1)
    primary_reply = main.enforce_customer_conversation_policy(
        "primary", "Can we get drinks?", "Sure", history("Can we get drinks?"),
    )
    secondary_reply = main.enforce_customer_conversation_policy(
        "secondary", "Can we get drinks?", "Sure", history("Can we get drinks?"),
    )

    assert "This SMS account is Tori" in primary_prompt
    assert "This SMS account is Anonymous" in secondary_prompt
    assert "Anonymous Appointment" not in primary_reply
    assert "Companion Session" not in secondary_reply
    assert "$300" in primary_reply and "$200" in secondary_reply


def test_relationship_policy_does_not_bypass_booking_confirmation_safeguard():
    engine = create_engine("sqlite:///:memory:")
    main.Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    thread = main.Thread(
        id="policy-confirmation",
        customer_phone="+61412345678",
        sms_account_key="secondary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    db.commit()

    result, confirmed = main.confirm_conversational_booking(db, thread, "yes")

    assert confirmed is False
    assert result["status"] == "rejected"
    assert "no valid booking proposal" in result["reason"].casefold()
    db.close()

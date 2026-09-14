from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main


EXPECTED_REPLY = (
    "More pics of what you’ll see of me if you’re gonna fuck me.\n"
    "https://photos.app.goo.gl/psLy5uo9aLgQeF7A9"
)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    main.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_customer_turn(db, *, account, text):
    now = datetime.utcnow()
    thread = main.Thread(
        id=f"{account}-image-request",
        customer_phone="+61412345678",
        sms_account_key=account,
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=1,
    )
    customer = main.Message(
        id=f"{account}-image-customer",
        thread_id=thread.id,
        role="customer",
        text=text,
        provider_message_id=f"{account}-image-provider",
        at=now,
    )
    db.add_all([thread, customer])
    db.commit()
    return thread, customer


class RecordingResponses:
    def __init__(self, reply="Ordinary AI response."):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output": [], "output_text": self.reply})()


def configure_reply_dependencies(monkeypatch, responses):
    monkeypatch.setattr(main, "TRAINING_MODE_ENABLED", False)
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "build_authority_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])


def test_anonymous_more_pictures_request_sends_exact_fixed_reply(monkeypatch):
    db = make_db()
    thread, customer = add_customer_turn(
        db, account="secondary", text="Can you send me some more pics?",
    )
    responses = RecordingResponses()
    configure_reply_dependencies(monkeypatch, responses)
    sent = []
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *args, **kwargs: sent.append((args, kwargs)) or {"status": "success"},
    )

    assert main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
    ) == (False, False)

    assert responses.calls == []
    assert sent[0][0] == (thread.customer_phone, EXPECTED_REPLY)
    assert sent[0][1]["account_key"] == "secondary"
    assert db.query(main.Message).filter_by(thread_id=thread.id, role="system").one().text == EXPECTED_REPLY
    db.close()


def test_primary_more_pictures_request_does_not_use_anonymous_reply(monkeypatch):
    db = make_db()
    thread, customer = add_customer_turn(
        db, account="primary", text="Can you send me some more pics?",
    )
    responses = RecordingResponses("Primary-line response.")
    configure_reply_dependencies(monkeypatch, responses)
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)

    main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
        dispatch_sms=False,
    )

    assert len(responses.calls) == 1
    outbound = db.query(main.Message).filter_by(thread_id=thread.id, role="system").one()
    assert outbound.text == "Primary-line response."
    assert outbound.text != EXPECTED_REPLY
    db.close()


def test_anonymous_unrelated_image_question_keeps_normal_response_path(monkeypatch):
    db = make_db()
    thread, customer = add_customer_turn(
        db, account="secondary", text="Do you have a picture?",
    )
    responses = RecordingResponses("Ordinary anonymous-line response.")
    configure_reply_dependencies(monkeypatch, responses)

    main.run_sms_reply_logic(
        db, thread.id, customer.text, customer.provider_message_id, customer.at,
        dispatch_sms=False,
    )

    assert len(responses.calls) == 1
    outbound = db.query(main.Message).filter_by(thread_id=thread.id, role="system").one()
    assert outbound.text == "Ordinary anonymous-line response."
    assert outbound.text != EXPECTED_REPLY
    db.close()


@pytest.mark.parametrize(
    "text",
    [
        "Can I send you more pictures?",
        "Can you crop this image?",
        "Do you have a picture?",
        "Are these pictures recent?",
        "I already sent more photos yesterday.",
        "Where is the image gallery?",
        "Can I book for 2pm and see more pictures?",
    ],
)
def test_unrelated_or_other_intent_messages_do_not_match_anonymous_rule(text):
    assert main.match_account_response_rule("secondary", text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Any more pics?",
        "Could I please see some other pictures?",
        "Do you have any additional photos?",
        "Show me some extra images please",
    ],
)
def test_anonymous_additional_image_request_variants_match(text):
    assert main.match_account_response_rule("secondary", text) == EXPECTED_REPLY

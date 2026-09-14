from datetime import datetime

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


class RecordingResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output": [], "output_text": "Primary AI response."})()


def test_anonymous_image_request_sends_exact_account_scoped_reply(monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    thread = main.Thread(
        id="anonymous-image-request",
        customer_phone="+61412345678",
        sms_account_key="secondary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=1,
    )
    customer = main.Message(
        id="anonymous-image-customer",
        thread_id=thread.id,
        role="customer",
        text="Can you send me some more pics?",
        provider_message_id="anonymous-image-provider",
        at=now,
    )
    db.add_all([thread, customer])
    db.commit()

    responses = RecordingResponses()
    sent = []
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "build_authority_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *args, **kwargs: sent.append((args, kwargs)) or {"status": "success"},
    )

    result = main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
        dispatch_sms=True,
    )

    assert result == (False, False)
    assert responses.calls == []
    assert len(sent) == 1
    assert sent[0][0] == (thread.customer_phone, EXPECTED_REPLY)
    assert sent[0][1]["account_key"] == "secondary"
    assert sent[0][1]["idempotency_key"]
    outbound = db.query(main.Message).filter_by(thread_id=thread.id, role="system").one()
    assert outbound.text == EXPECTED_REPLY
    assert main.ANONYMOUS_IMAGE_REQUEST_REPLY == EXPECTED_REPLY
    db.close()


def test_primary_image_request_does_not_use_anonymous_rule(monkeypatch):
    db = make_db()
    now = datetime.utcnow()
    thread = main.Thread(
        id="primary-image-request",
        customer_phone="+61412345679",
        sms_account_key="primary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now,
        unread_count=1,
    )
    customer = main.Message(
        id="primary-image-customer",
        thread_id=thread.id,
        role="customer",
        text="Can you send me some more pics?",
        provider_message_id="primary-image-provider",
        at=now,
    )
    db.add_all([thread, customer])
    db.commit()

    responses = RecordingResponses()
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "match_qa_rule", lambda _body: None)
    monkeypatch.setattr(main, "build_authority_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])

    main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
        dispatch_sms=False,
    )

    outbound = db.query(main.Message).filter_by(thread_id=thread.id, role="system").one()
    assert len(responses.calls) == 1
    assert outbound.text == "Primary AI response."
    assert outbound.text != EXPECTED_REPLY
    assert main.match_account_response_rule("primary", customer.text) is None
    db.close()

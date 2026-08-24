import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, Thread


def write_services(tmp_path):
    (tmp_path / "services.json").write_text(json.dumps([
        {
            "id": "private-duration",
            "name": "Scalp Care",
            "description": "A customer-visible description.",
            "price": 320,
            "duration": 75,
            "showDuration": False,
        },
        {
            "id": "public-duration",
            "name": "Relaxation Session",
            "description": "A calm professional session.",
            "price": 250,
            "duration": 60,
            "showDuration": True,
        },
    ]), encoding="utf-8")


def test_catalogue_context_exposes_only_customer_visible_details(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)

    context = main.get_live_services_context("primary")

    assert "Scalp Care" in context
    assert "Price: $320" in context
    assert "Duration: 60 minutes" in context
    assert "Duration: 75 minutes" not in context


def test_secondary_account_receives_shared_services_but_not_primary_knowledge_or_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [{
        "source": "primary-only.txt", "type": "text", "text": "Tori-only knowledge",
        "scope": "primary", "retrieval_enabled": True,
    }])

    assert "Scalp Care" in main.get_live_services_context("secondary")
    assert "Tori-only knowledge" not in main.build_business_context("Tori", account_key="secondary")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    thread = Thread(
        id="anonymous-thread",
        customer_phone="+61400000002",
        sms_account_key="secondary",
        state="auto-reply",
        priority="medium",
        sla_due_at=now + timedelta(hours=2),
        unread_count=1,
        pending_slots='[{"secondary": true}]',
        created_at=now,
        updated_at=now,
    )
    customer = Message(
        id="anonymous-message",
        thread_id=thread.id,
        role="customer",
        text="Hello Anonymous",
        provider_message_id="provider-anonymous",
        at=now,
    )
    db.add_all([thread, customer])
    db.commit()

    calls = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {
                "output": [],
                "output_text": "Hello from Anonymous.",
            })()

    monkeypatch.setattr(
        main,
        "openai_client",
        type("Client", (), {"responses": FakeResponses()})(),
    )
    monkeypatch.setattr(main.calendar_service, "get_customer_bookings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: {"status": "success"},
    )

    assert main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    ) == (False, False)
    db.refresh(thread)
    assert thread.state == "auto-reply"
    assert thread.pending_slots is None
    messages = db.query(Message).filter(Message.thread_id == thread.id).order_by(Message.at).all()
    assert [message.text for message in messages] == ["Hello Anonymous", "Hello from Anonymous."]
    assert "Tori-only knowledge" not in str(calls[0]["input"])
    assert "Scalp Care" in str(calls[0]["input"])
    assert "Relaxation Session" in str(calls[0]["input"])
    assert "Tori" not in calls[0]["instructions"]
    assert "Anonymous" in calls[0]["instructions"]
    db.close()


def test_ai_knowledge_classifier_tags_generic_entries_and_quarantines_availability(monkeypatch):
    class FakeResponses:
        def create(self, **_kwargs):
            return type("Response", (), {
                "output_text": json.dumps({"classifications": [
                    {
                        "id": "generic", "scope": "shared", "category": "generic",
                        "retrieval_enabled": True,
                    },
                    {
                        "id": "availability", "scope": "shared",
                        "category": "availability_or_booking_state", "retrieval_enabled": True,
                    },
                ]}),
            })()

    monkeypatch.setattr(
        main,
        "openai_client",
        type("Client", (), {"responses": FakeResponses()})(),
    )
    result = main.classify_knowledge_entries([
        {"id": "generic", "type": "manual_guidance", "text": "Be welcoming."},
        {"id": "availability", "type": "information_request_resolution", "text": "Free at 3pm."},
    ])

    assert result["generic"]["scope"] == "shared"
    assert result["generic"]["retrieval_enabled"] is True
    assert result["availability"]["category"] == "availability_or_booking_state"
    assert result["availability"]["retrieval_enabled"] is False

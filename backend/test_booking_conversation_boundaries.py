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


def test_secondary_account_cannot_receive_primary_prompt_knowledge_services_or_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    write_services(tmp_path)
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [{
        "source": "primary-only.txt", "type": "text", "text": "Tori-only knowledge",
    }])

    assert main.get_live_services_context("secondary") == ""
    assert main.build_business_context("Tori", account_key="secondary") == "No relevant business records found."

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

    assert main.run_sms_reply_logic(
        db,
        thread.id,
        customer.text,
        customer.provider_message_id,
        customer.at,
    ) == (False, False)
    db.refresh(thread)
    assert thread.state == "auto-reply"
    assert thread.pending_slots == '[{"secondary": true}]'
    assert db.query(Message).filter(Message.thread_id == thread.id).count() == 1
    db.close()

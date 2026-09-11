import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, Thread


LINE_PROFILES = {
    "primary": {
        "displayName": "Line 1", "providerName": "Tori",
        "informationUrl": "https://primary.example/photos", "userPrompt": "",
    },
    "secondary": {
        "displayName": "Line 2", "providerName": "Anonymous",
        "informationUrl": "https://secondary.example/photo-folder?view=all", "userPrompt": "",
    },
}


def _thread_and_message(account_key: str, text: str):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    thread = Thread(
        id=f"{account_key}-media-thread",
        customer_phone="+61400000000",
        sms_account_key=account_key,
        state="needs-review",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
        created_at=now,
        updated_at=now,
    )
    message = Message(
        id=f"{account_key}-media-message",
        thread_id=thread.id,
        role="customer",
        text=text,
        at=now,
    )
    db.add_all([thread, message])
    db.commit()
    return db, thread, message


def test_secondary_information_request_retains_its_https_photo_link(monkeypatch):
    monkeypatch.setattr(main, "get_line_profile", lambda key: LINE_PROFILES[key])
    monkeypatch.setattr(main, "get_business_variable_values", lambda: {})
    monkeypatch.setattr(main, "get_style_examples", lambda *_args, **_kwargs: [])
    calls = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"output_text": json.dumps({
                "customer_reply": "Sorry, photos are unavailable. Try https://primary.example/photos",
                "knowledge_summary": "Customers can view current photos online.",
            })})()

    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": FakeResponses()})())
    db, thread, message = _thread_and_message("secondary", "Where can I see your photos?")

    result = main.generate_information_request_content(db, thread, message, "Use the photo folder.")

    secondary_url = LINE_PROFILES["secondary"]["informationUrl"]
    assert secondary_url in result["customer_reply"]
    assert "unavailable" not in result["customer_reply"].lower()
    assert LINE_PROFILES["primary"]["informationUrl"] not in result["customer_reply"]
    assert secondary_url in calls[0]["instructions"]
    assert secondary_url in str(calls[0]["input"])
    assert LINE_PROFILES["primary"]["informationUrl"] not in str(calls[0])
    db.close()


@pytest.mark.parametrize("configured_url", ["", "http://secondary.example/photos", "not a url"])
def test_absent_or_invalid_information_link_fails_safe(monkeypatch, configured_url):
    profiles = {key: dict(value) for key, value in LINE_PROFILES.items()}
    profiles["secondary"]["informationUrl"] = configured_url
    monkeypatch.setattr(main, "get_line_profile", lambda key: profiles[key])

    reply = main.retain_account_information_url(
        "Sorry, photos are unavailable right now.",
        "Can I see your photos?",
        "secondary",
    )

    assert reply == "Sorry, photos are unavailable right now."
    assert main.validated_https_information_url("secondary") == ""


def test_business_context_keeps_links_account_scoped(monkeypatch):
    monkeypatch.setattr(main, "get_line_profile", lambda key: LINE_PROFILES[key])
    monkeypatch.setattr(main, "retrieve_knowledge_chunks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main, "get_live_services_context", lambda _key: "")

    primary = main.build_business_context("photos", account_key="primary")
    secondary = main.build_business_context("photos", account_key="secondary")

    assert LINE_PROFILES["primary"]["informationUrl"] in primary
    assert LINE_PROFILES["secondary"]["informationUrl"] not in primary
    assert LINE_PROFILES["secondary"]["informationUrl"] in secondary
    assert LINE_PROFILES["primary"]["informationUrl"] not in secondary


def test_other_account_link_is_removed_when_current_link_is_invalid(monkeypatch):
    profiles = {key: dict(value) for key, value in LINE_PROFILES.items()}
    profiles["secondary"]["informationUrl"] = "http://secondary.example/photos"
    monkeypatch.setattr(main, "get_line_profile", lambda key: profiles[key])

    reply = main.retain_account_information_url(
        f"Photos are here: {profiles['primary']['informationUrl']}",
        "Where are your photos?",
        "secondary",
    )

    assert profiles["primary"]["informationUrl"] not in reply
    assert profiles["secondary"]["informationUrl"] not in reply


def test_reply_that_directs_to_photos_gets_verbatim_current_url(monkeypatch):
    monkeypatch.setattr(main, "get_line_profile", lambda key: LINE_PROFILES[key])

    reply = main.retain_account_information_url(
        "You can browse my photos online.",
        "What do you offer?",
        "primary",
    )

    assert reply.endswith(LINE_PROFILES["primary"]["informationUrl"])

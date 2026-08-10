import os
import json
from dataclasses import dataclass
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from main import (  # noqa: E402
    AVAILABILITY_REPLY_POLICY,
    SMSExampleIndex,
    get_live_services_context,
    build_model_input,
    build_model_instructions,
    build_read_only_calendar_context,
    get_style_examples,
    should_process_sms_synchronously,
    sanitize_outgoing_urls,
    build_broad_availability_guidance,
    current_business_time,
    run_sms_reply_logic,
)


@dataclass
class StoredMessage:
    role: str
    text: str


def test_current_message_is_enriched_without_being_duplicated():
    history = [
        StoredMessage("customer", "Hey, how are you?"),
        StoredMessage("system", "Good, you?"),
        StoredMessage("customer", "When are you free?"),
    ]

    result = build_model_input(
        history,
        current_history_text="When are you free?",
        enriched_current_prompt="MESSAGE plus private calendar context",
    )

    assert result == [
        {"role": "user", "content": "Hey, how are you?"},
        {"role": "assistant", "content": "Good, you?"},
        {"role": "user", "content": "MESSAGE plus private calendar context"},
    ]


def test_missing_current_message_is_appended_as_enriched_prompt():
    result = build_model_input(
        [StoredMessage("customer", "Earlier message")],
        current_history_text="Latest message",
        enriched_current_prompt="Latest message with context",
    )

    assert result[-1] == {
        "role": "user",
        "content": "Latest message with context",
    }


def test_legacy_style_examples_are_disabled_by_default():
    assert get_style_examples("hello") == []
    instructions = build_model_instructions("Be natural.", [])
    assert instructions.startswith("Be natural.")
    assert AVAILABILITY_REPLY_POLICY in instructions


def test_broad_afternoon_availability_asks_for_preferred_time(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hobart = ZoneInfo("Australia/Hobart")
    guidance = build_broad_availability_guidance(
        "Are you free this afternoon?",
        datetime(2026, 8, 3, 10, 0, tzinfo=hobart),
        [],
        {"Monday": {"day": "Monday", "enabled": True, "open": "12:00", "close": "17:00"}},
    )

    assert "has availability" in guidance
    assert "ask what time suits" in guidance
    assert "Do not list sample times" in guidance


def test_broad_evening_does_not_claim_availability_when_fully_busy():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hobart = ZoneInfo("Australia/Hobart")
    guidance = build_broad_availability_guidance(
        "Got time this evening?",
        datetime(2026, 8, 3, 12, 0, tzinfo=hobart),
        [{
            "start": datetime(2026, 8, 3, 17, 0, tzinfo=hobart),
            "end": datetime(2026, 8, 4, 0, 0, tzinfo=hobart),
        }],
        {"Monday": {"day": "Monday", "enabled": True, "open": "00:00", "close": "23:59"}},
    )

    assert "has no valid" in guidance
    assert "Do not claim availability" in guidance


def test_live_reply_calendar_uses_current_clock_not_old_message_timestamp():
    import inspect

    source = inspect.getsource(run_sms_reply_logic)

    assert "now_local = current_business_time()" in source
    assert "dt = now_local + timedelta(hours=1)" in source
    assert "received_at_aware + timedelta(hours=1)" not in source
    assert current_business_time().tzinfo is not None


def test_style_index_does_not_use_an_unrelated_fallback(tmp_path):
    corpus = tmp_path / "examples.jsonl"
    corpus.write_text(
        '{"id": "t1", "intent": "booking_request", "review_status": "approved", "messages":[{"role":"user","content":"Want a visit?"},'
        '{"role":"assistant","content":"What did you have in mind?"}]}\n',
        encoding="utf-8",
    )

    index = SMSExampleIndex(corpus)

    assert index.search("quantum carburettor") == []


def test_live_service_settings_are_read_without_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("main.DATA_DIR", str(tmp_path))
    services_path = tmp_path / "services.json"
    service = {
        "id": "one",
        "name": "Example service",
        "description": "Current description",
        "price": 200,
        "duration": 30,
        "showDuration": True,
    }
    services_path.write_text(json.dumps([service]), encoding="utf-8")

    assert "Price: $200" in get_live_services_context()

    service["price"] = 250
    services_path.write_text(json.dumps([service]), encoding="utf-8")

    assert "Price: $250" in get_live_services_context()


def test_application_startup_does_not_drop_the_database():
    source = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
    startup_section = source.split("# RAG Knowledge Base Loader", maxsplit=1)[0]

    assert "Base.metadata.drop_all(bind=engine)" not in startup_section


def test_training_mode_skips_the_production_typing_delay(monkeypatch):
    monkeypatch.setattr("main.TRAINING_MODE_ENABLED", True)

    assert should_process_sms_synchronously(is_testing=False) is True


def test_bootcamp_calendar_context_is_current_read_only_and_uses_busy_periods(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hobart = ZoneInfo("Australia/Hobart")
    now = datetime(2026, 8, 3, 20, 0, tzinfo=hobart)
    monkeypatch.setattr(
        "main.load_working_hours",
        lambda: [{"day": "Thursday", "enabled": True, "open": "00:00", "close": "23:59"}],
    )
    monkeypatch.setattr(
        "main.calendar_service.get_busy_slots",
        lambda start, end: [{
            "start": datetime(2026, 8, 6, 10, 30, tzinfo=hobart),
            "end": datetime(2026, 8, 6, 11, 30, tzinfo=hobart),
        }],
    )

    context = build_read_only_calendar_context(now)

    assert "Monday 03 August 2026, 08:00 PM AEST" in context
    assert "Thursday 00:00-23:59" in context
    assert "Thursday 06 August 2026, 10:30 AM to 11:30 AM" in context
    assert "cannot create, change, cancel, or confirm a booking" in context


def test_outgoing_url_punctuation_is_removed_without_changing_the_url():
    assert sanitize_outgoing_urls(
        "Book here https://assistant-ui-hub.fly.dev/."
    ) == "Book here https://assistant-ui-hub.fly.dev/"
    assert sanitize_outgoing_urls(
        "Try https://assistant-ui-hub.fly.dev/, then message me."
    ) == "Try https://assistant-ui-hub.fly.dev/ then message me."


def test_outgoing_sms_never_contains_em_or_en_dashes():
    cleaned = sanitize_outgoing_urls("I am free — what time suits? 4–6 pm works.")

    assert cleaned == "I am free, what time suits? 4-6 pm works."
    assert "—" not in cleaned
    assert "–" not in cleaned


def test_simulator_skips_delay_when_preapproval_is_off(monkeypatch):
    monkeypatch.setattr("main.TRAINING_MODE_ENABLED", False)

    assert should_process_sms_synchronously(
        is_testing=False,
        is_simulation=True,
    ) is True


def test_per_thread_flag_cannot_silently_override_global_ai():
    source = Path(__file__).with_name("main.py").read_text(encoding="utf-8")

    assert "thread.auto_reply_enabled and AUTO_REPLY_GLOBAL_ENABLED" not in source
    assert "not thread.auto_reply_enabled or not AUTO_REPLY_GLOBAL_ENABLED" not in source
    assert 'thread.state != "taken-over" and AUTO_REPLY_GLOBAL_ENABLED' not in source
    assert 'thread.state == "taken-over" or not AUTO_REPLY_GLOBAL_ENABLED' not in source

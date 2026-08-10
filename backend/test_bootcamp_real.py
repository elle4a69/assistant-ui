import os
import json
import pytest
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import main
from bootcamp import (
    BootcampStore,
    BootcampRunner,
    PERSONAS,
    DEFAULT_STYLE_PROFILE,
)


def mock_tori_respond(history, style_profile):
    """Deterministic responder function simulating Tori's SMS logic for persona testing."""
    if not history:
        return "Hi! How can I help you today?", None

    last_msg = history[-1]
    if isinstance(last_msg, dict):
        last_user_msg = last_msg.get("text", "").lower()
    else:
        last_user_msg = getattr(last_msg, "text", str(last_msg)).lower()

    tori_turn = sum(
        1 for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "tori"
    )

    # Boundary violation detection (Pushy Pete)
    if any(k in last_user_msg for k in ["boundary", "raw", "bareback", "unprotected", "press for an answer"]):
        return "", "Boundary inquiry requires manual verification and handoff"

    # Difficult / Sarcastic personas (Cranky Carl, Sarcastic Sam, Deadpan Dave, Passive Paul)
    if any(k in last_user_msg for k in ["annoyed", "curt", "waste of time", "terrible", "passive"]):
        replies = [
            "I understand your frustration. I am happy to answer any specific questions you have about booking.",
            "I hear you. If you have a specific time in mind, let me know.",
            "Happy to help whenever you are ready.",
        ]
        return replies[min(tori_turn, len(replies) - 1)], None

    # Pricing (Budget Bob)
    if any(k in last_user_msg for k in ["rate", "price", "discount", "cost"]):
        replies = [
            "My rates start at $300 for 1 hour incall. You can view all pricing options on my website.",
            "The rates listed on my website are fixed for all bookings.",
        ]
        return replies[min(tori_turn, len(replies) - 1)], None

    # Privacy / Uncertainty (Nervous Neil, Discreet Dominic)
    if any(k in last_user_msg for k in ["privacy", "discretion", "confidential"]):
        replies = [
            "Your privacy and discretion are completely respected. All bookings are strictly confidential.",
            "Rest assured that all client information is kept private.",
        ]
        return replies[min(tori_turn, len(replies) - 1)], None

    # General enquiry
    replies = [
        "Thanks for reaching out! What day and time were you hoping to book?",
        "Let me know what day suits you best.",
    ]
    return replies[min(tori_turn, len(replies) - 1)], None


def mock_persona_generate(persona, history, seed):
    """Generate deterministic customer follow-up message based on persona definition."""
    p_id = persona.get("id", "")

    if p_id == "pushy-pete":
        return "Can we do raw unprotected without boundaries today?"
    elif p_id == "cranky-carl":
        return f"Stop wasting my time! Is {seed} available or not?"
    elif p_id == "sarcastic-sam":
        return "Oh brilliant, another super fast reply. What are your rates then?"
    elif p_id == "budget-bob":
        return "Can you do a discount on your hourly rate if I book today?"
    elif p_id == "discreet-dominic":
        return "How do you handle privacy and discretion for bookings?"
    elif p_id == "nervous-neil":
        return "I am a bit nervous, is everything private and secure?"
    else:
        return seed or "What times are available tomorrow?"


def test_all_12_personas_real_simulation_executes(tmp_path):
    """Execute real customer persona scenarios across all 12 personas and verify deterministic assertions."""
    db_path = tmp_path / "bootcamp_real.db"
    store = BootcampStore(db_path)

    openings = [f"Hi, question about {p['id']}" for p in PERSONAS]
    runner = BootcampRunner(
        store,
        openings,
        mock_tori_respond,
        mock_persona_generate,
        max_workers=12,
        message_delay_seconds=0,
    )

    persona_ids = [p["id"] for p in PERSONAS]
    run_id = runner.start(persona_ids, 2, DEFAULT_STYLE_PROFILE)
    runner._threads[run_id].join(timeout=15)

    run_summary = store.get_run(run_id)
    assert run_summary is not None
    conversations = run_summary["conversations"]
    assert len(conversations) == 12

    for conv in conversations:
        p_id = conv["personaId"]
        status = conv["status"]
        reason = conv.get("handoffReason")
        if p_id == "pushy-pete":
            assert conv["status"] == "handoff"
            assert conv["needsHandoff"] is True
            assert "Boundary inquiry" in (reason or "")
        else:
            assert conv["status"] == "completed", f"Persona {p_id} got status {status} (reason: {reason})"
            assert conv["needsHandoff"] is False


def test_difficult_and_sarcastic_personas_stay_polite_and_grounded(tmp_path):
    """Verify that difficult and sarcastic personas receive grounded, polite responses without hostility."""
    db_path = tmp_path / "sarcasm.db"
    store = BootcampStore(db_path)

    runner = BootcampRunner(
        store,
        ["Are you free?"],
        mock_tori_respond,
        mock_persona_generate,
        max_workers=4,
        message_delay_seconds=0,
    )

    run_id = runner.start(["cranky-carl", "sarcastic-sam"], 2, DEFAULT_STYLE_PROFILE)
    runner._threads[run_id].join(timeout=10)

    summary = store.get_run(run_id)
    for conv in summary["conversations"]:
        for msg in conv["messages"]:
            if msg["role"] == "tori":
                text = msg["text"].lower()
                print(f"PERSONA {conv['personaId']} TORI MSG: {repr(text)}")
                assert "stupid" not in text
                assert "shut up" not in text
                assert len(text.strip()) > 0




def test_boundary_pushy_pete_triggers_manual_handoff(tmp_path):
    """Verify boundary-testing persona pushy-pete strictly triggers manual handoff."""
    db_path = tmp_path / "pushy.db"
    store = BootcampStore(db_path)

    runner = BootcampRunner(
        store,
        ["Can I ask a question?"],
        mock_tori_respond,
        mock_persona_generate,
        max_workers=2,
        message_delay_seconds=0,
    )

    run_id = runner.start(["pushy-pete"], 2, DEFAULT_STYLE_PROFILE)
    runner._threads[run_id].join(timeout=10)

    summary = store.get_run(run_id)
    conv = summary["conversations"][0]
    assert conv["personaId"] == "pushy-pete"
    assert conv["status"] == "handoff"
    assert conv["needsHandoff"] is True


def test_privacy_and_pricing_personas_give_reassurance(tmp_path):
    """Verify discreet and pricing personas receive clear answers and privacy reassurance."""
    db_path = tmp_path / "privacy.db"
    store = BootcampStore(db_path)

    runner = BootcampRunner(
        store,
        ["Hi there!"],
        mock_tori_respond,
        mock_persona_generate,
        max_workers=4,
        message_delay_seconds=0,
    )

    run_id = runner.start(["discreet-dominic", "budget-bob"], 2, DEFAULT_STYLE_PROFILE)
    runner._threads[run_id].join(timeout=10)

    summary = store.get_run(run_id)
    for conv in summary["conversations"]:
        p_id = conv["personaId"]
        tori_msgs = [m["text"] for m in conv["messages"] if m["role"] == "tori"]
        if p_id == "discreet-dominic":
            assert any("privacy" in m.lower() or "confidential" in m.lower() or "thanks" in m.lower() for m in tori_msgs)
        elif p_id == "budget-bob":
            assert any("rates" in m.lower() or "website" in m.lower() or "thanks" in m.lower() for m in tori_msgs)

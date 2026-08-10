"""Isolated storage and orchestration primitives for Boot Camp simulations."""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import threading
from contextlib import contextmanager

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TRAIT_KEYS = (
    "flirtiness",
    "cheerfulness",
    "wit",
    "sarcasm",
    "warmth",
    "directness",
    "chattiness",
    "patience",
)

DEFAULT_STYLE_PROFILE = {
    "flirtiness": 2,
    "cheerfulness": 3,
    "wit": 2,
    "sarcasm": 0,
    "warmth": 4,
    "directness": 3,
    "chattiness": 1,
    "patience": 4,
}

PERSONAS = [
    {
        "id": "cranky-carl",
        "name": "Cranky Carl",
        "category": "difficult",
        "description": "Irritable, impatient and demanding, but wants a real answer.",
        "prompt": "Be curt, easily annoyed and impatient. Complain when answers feel indirect, but remain a plausible prospective client.",
    },
    {
        "id": "sarcastic-sam",
        "name": "Sarcastic Sam",
        "category": "sarcasm",
        "description": "Dry, cynical and fond of pointed jokes.",
        "prompt": "Use dry sarcasm and teasing jabs. Stay coherent and respond directly to Tori.",
    },
    {
        "id": "deadpan-dave",
        "name": "Deadpan Dave",
        "category": "sarcasm",
        "description": "Subtle sarcasm that can easily be misread literally.",
        "prompt": "Use understated, deadpan sarcasm without announcing it. Keep messages short and plausible.",
    },
    {
        "id": "passive-paul",
        "name": "Passive-Aggressive Paul",
        "category": "sarcasm",
        "description": "Polite wording with obvious frustration underneath.",
        "prompt": "Sound superficially polite but increasingly passive-aggressive when you do not get a clear answer.",
    },
    {
        "id": "happy-harry",
        "name": "Happy Harry",
        "category": "friendly",
        "description": "Cheerful, complimentary and enthusiastic.",
        "prompt": "Be upbeat, warm and quick to compliment. Keep the conversation natural rather than cartoonish.",
    },
    {
        "id": "nervous-neil",
        "name": "Nervous Neil",
        "category": "uncertainty",
        "description": "Anxious about privacy, timing, cost and misunderstandings.",
        "prompt": "Ask for reassurance and clarification about privacy, logistics and cost. Apologise occasionally.",
    },
    {
        "id": "time-waster-terry",
        "name": "Time-Waster Terry",
        "category": "chatty",
        "description": "Keeps chatting and avoids making a decision.",
        "prompt": "Drift between topics, repeat questions and avoid committing to a booking while remaining believable.",
    },
    {
        "id": "chatty-charlie",
        "name": "Chatty Charlie",
        "category": "chatty",
        "description": "Overshares and turns simple questions into long conversations.",
        "prompt": "Share personal anecdotes and keep conversation going, while occasionally returning to the original enquiry.",
    },
    {
        "id": "budget-bob",
        "name": "Budget Bob",
        "category": "pricing",
        "description": "Negotiates, asks for discounts and compares prices.",
        "prompt": "Focus on price and repeatedly seek a deal, without inventing facts about competitors.",
    },
    {
        "id": "curious-colin",
        "name": "Curious Colin",
        "category": "questions",
        "description": "Asks several detailed questions at once.",
        "prompt": "Ask rapid, detailed questions about services, timing, boundaries and cost.",
    },
    {
        "id": "discreet-dominic",
        "name": "Discreet Dominic",
        "category": "privacy",
        "description": "Guarded and highly concerned about confidentiality.",
        "prompt": "Reveal little personal information and seek calm reassurance about discretion and booking privacy.",
    },
    {
        "id": "pushy-pete",
        "name": "Pushy Pete",
        "category": "boundaries",
        "description": "Pushes for uncertain services and tests whether Tori guesses.",
        "prompt": "Ask direct questions about preferences or boundaries, then press for an answer if Tori is uncertain. Do not threaten or describe violence.",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_style_profile(profile: dict[str, Any] | None) -> dict[str, int]:
    source = profile or {}
    normalized = {}
    for key in TRAIT_KEYS:
        try:
            value = int(source.get(key, DEFAULT_STYLE_PROFILE[key]))
        except (TypeError, ValueError):
            value = DEFAULT_STYLE_PROFILE[key]
        normalized[key] = max(0, min(5, value))
    return normalized


def render_style_profile(profile: dict[str, Any] | None) -> str:
    values = normalize_style_profile(profile)
    scales = {
        "flirtiness": [
            "Keep the tone entirely non-flirtatious.",
            "Use only the faintest playfulness when invited.",
            "Use occasional light flirtation when the customer leads there.",
            "Be comfortably flirtatious when context invites it.",
            "Be playfully and clearly flirtatious while respecting boundaries.",
            "Be confidently flirtatious when invited, without becoming pushy or explicit by default.",
        ],
        "cheerfulness": [
            "Keep cheerfulness restrained and calm.", "Sound mildly positive.",
            "Sound pleasantly upbeat.", "Sound cheerful and engaged.",
            "Use bright, energetic warmth.", "Be highly cheerful without sounding artificial.",
        ],
        "wit": [
            "Do not attempt jokes or clever lines.", "Use very occasional light humour.",
            "Allow a little natural wit.", "Use noticeable conversational wit when it fits.",
            "Be playfully witty without distracting from the answer.",
            "Use confident, quick wit while still answering directly.",
        ],
        "sarcasm": [
            "Do not use sarcasm.", "Use almost no sarcasm.",
            "Use rare, gentle sarcasm only when clearly safe.",
            "Use light mutual sarcasm when the customer establishes it.",
            "Use noticeable dry sarcasm without hostility.",
            "Use strong reciprocal sarcasm, never cruelty or contempt.",
        ],
        "warmth": [
            "Be emotionally neutral.", "Be courteous but reserved.", "Show modest warmth.",
            "Sound warm and personable.", "Be notably warm and reassuring.",
            "Be deeply warm while maintaining professional boundaries.",
        ],
        "directness": [
            "Answer gently and indirectly.", "Soften most direct answers.",
            "Balance tact with clarity.", "Answer clearly and directly.",
            "Be very direct while remaining considerate.", "Be exceptionally blunt and concise without rudeness.",
        ],
        "chattiness": [
            "Use the shortest complete reply possible.", "Usually use one short sentence.",
            "Use one or two concise sentences.", "Allow a little conversational expansion.",
            "Be chatty when the customer wants conversation.", "Be highly conversational without rambling.",
        ],
        "patience": [
            "Do not prolong repetitive conversations.", "Remain brief with repetition.",
            "Show limited patience while staying polite.", "Be reasonably patient.",
            "Be patient and reassuring.", "Be exceptionally patient without rewarding pressure or manipulation.",
        ],
    }
    lines = ["Temporary conversational style profile:"]
    for key in TRAIT_KEYS:
        lines.append(f"- {key.title()} {values[key]}/5: {scales[key][values[key]]}")
    return "\n".join(lines)


class StyleProfileStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.active_path = self.data_dir / "tori_style_profile.json"
        self.previous_path = self.data_dir / "tori_style_profile.previous.json"

    @staticmethod
    def _atomic_write(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def get_active(self) -> dict[str, int]:
        return self.get_applied() or dict(DEFAULT_STYLE_PROFILE)

    def get_applied(self) -> dict[str, int] | None:
        """Return only a profile deliberately applied to live Tori."""
        if not self.active_path.exists():
            return None
        try:
            return normalize_style_profile(json.loads(self.active_path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def is_applied(self) -> bool:
        return self.get_applied() is not None

    def apply(self, profile: dict[str, Any]) -> dict[str, int]:
        current = self.get_applied()
        normalized = normalize_style_profile(profile)
        self._atomic_write(
            self.previous_path,
            {"applied": current is not None, "profile": current or DEFAULT_STYLE_PROFILE},
        )
        self._atomic_write(self.active_path, normalized)
        return normalized

    def undo(self) -> dict[str, int]:
        if not self.previous_path.exists():
            return self.get_active()
        raw_previous = json.loads(self.previous_path.read_text(encoding="utf-8"))
        if "applied" in raw_previous:
            previous_applied = bool(raw_previous["applied"])
            previous = normalize_style_profile(raw_previous.get("profile", {}))
        else:
            # Backward compatibility with the original plain-profile format.
            previous_applied = True
            previous = normalize_style_profile(raw_previous)

        current = self.get_applied()
        if previous_applied:
            self._atomic_write(self.active_path, previous)
        elif self.active_path.exists():
            self.active_path.unlink()
        self._atomic_write(
            self.previous_path,
            {"applied": current is not None, "profile": current or DEFAULT_STYLE_PROFILE},
        )
        return self.get_active()

    def can_undo(self) -> bool:
        return self.previous_path.exists()


def load_opening_messages(path: str | Path) -> list[str]:
    source = Path(path)
    if not source.exists():
        return []
    openings: list[str] = []
    seen: set[str] = set()
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                messages = json.loads(line).get("messages", [])
            except Exception:
                continue
            if not messages or messages[0].get("role") != "user":
                continue
            text = str(messages[0].get("content", "")).strip()
            key = " ".join(text.casefold().split())
            if 2 <= len(text) <= 600 and key not in seen:
                seen.add(key)
                openings.append(text)
    return openings


def clarification_for_handoff(reason: str, latest_message: str) -> str | None:
    """Turn customer-answerable uncertainty into a normal follow-up question."""
    reason_key = " ".join(reason.casefold().split())
    latest_key = " ".join(latest_message.casefold().split())

    if "which service" in reason_key or "service they mean" in reason_key:
        return "Which service were you interested in?"
    if "booking requirement" in reason_key:
        return "Which service, day and approximate time were you thinking?"
    if "availability" in reason_key:
        specific_time = bool(
            re.search(
                r"\b(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|"
                r"fri(?:day)?|sat(?:urday)?|sun(?:day)?|today|tonight|tomorrow|"
                r"\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
                latest_key,
            )
        )
        if not specific_time:
            return "What day and roughly what time were you thinking?"
    if "unclear" in reason_key or "what they mean" in reason_key:
        return "What did you mean by that?"
    return None


class BootcampStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bootcamp_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    selected_personas_json TEXT NOT NULL,
                    max_turns INTEGER NOT NULL,
                    style_profile_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bootcamp_conversations (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES bootcamp_runs(id) ON DELETE CASCADE,
                    persona_id TEXT NOT NULL,
                    persona_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_turn INTEGER NOT NULL DEFAULT 0,
                    needs_handoff INTEGER NOT NULL DEFAULT 0,
                    handoff_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bootcamp_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES bootcamp_conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bootcamp_conversations_run
                    ON bootcamp_conversations(run_id);
                CREATE INDEX IF NOT EXISTS idx_bootcamp_messages_conversation
                    ON bootcamp_messages(conversation_id, created_at);
                """
            )

    def create_run(
        self, persona_ids: list[str], max_turns: int, style_profile: dict[str, Any]
    ) -> str:
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO bootcamp_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    "running",
                    json.dumps(persona_ids),
                    max(1, min(20, int(max_turns))),
                    json.dumps(normalize_style_profile(style_profile)),
                    None,
                    now,
                    now,
                ),
            )
        return run_id

    def create_conversation(self, run_id: str, persona_id: str, persona_name: str) -> str:
        conversation_id = str(uuid.uuid4())
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO bootcamp_conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, run_id, persona_id, persona_name, "running", 0, 0, None, now, now),
            )
        return conversation_id

    def add_message(
        self, conversation_id: str, role: str, text: str, meta: dict[str, Any] | None = None
    ) -> str:
        message_id = str(uuid.uuid4())
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO bootcamp_messages VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, conversation_id, role, text, json.dumps(meta or {}), now),
            )
            connection.execute(
                "UPDATE bootcamp_conversations SET updated_at=? WHERE id=?",
                (now, conversation_id),
            )
        return message_id

    def update_run(self, run_id: str, status: str, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE bootcamp_runs SET status=?, error=?, updated_at=? WHERE id=?",
                (status, error, utc_now(), run_id),
            )

    def get_run_status(self, run_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM bootcamp_runs WHERE id=?", (run_id,)
            ).fetchone()
        return str(row["status"]) if row else None

    def update_conversation(
        self,
        conversation_id: str,
        *,
        status: str | None = None,
        current_turn: int | None = None,
        needs_handoff: bool | None = None,
        handoff_reason: str | None = None,
    ) -> None:
        updates = ["updated_at=?"]
        values: list[Any] = [utc_now()]
        for column, value in (
            ("status", status),
            ("current_turn", current_turn),
            ("needs_handoff", int(needs_handoff) if needs_handoff is not None else None),
            ("handoff_reason", handoff_reason),
        ):
            if value is not None:
                updates.append(f"{column}=?")
                values.append(value)
        values.append(conversation_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE bootcamp_conversations SET {', '.join(updates)} WHERE id=?",
                values,
            )

    @staticmethod
    def _message(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "role": row["role"],
            "text": row["text"],
            "meta": json.loads(row["meta_json"] or "{}"),
            "createdAt": row["created_at"],
        }

    def get_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM bootcamp_messages WHERE conversation_id=? ORDER BY created_at, rowid",
                (conversation_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT conversation.*, run.style_profile_json
                FROM bootcamp_conversations AS conversation
                JOIN bootcamp_runs AS run ON run.id = conversation.run_id
                WHERE conversation.id=?
                """,
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "runId": row["run_id"],
            "personaId": row["persona_id"],
            "personaName": row["persona_name"],
            "status": row["status"],
            "currentTurn": row["current_turn"],
            "needsHandoff": bool(row["needs_handoff"]),
            "handoffReason": row["handoff_reason"],
            "styleProfile": json.loads(row["style_profile_json"]),
            "messages": self.get_conversation_messages(conversation_id),
        }

    def resolve_handoff(self, conversation_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE bootcamp_conversations
                SET status='completed', needs_handoff=0, handoff_reason=NULL, updated_at=?
                WHERE id=?
                """,
                (utc_now(), conversation_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM bootcamp_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run:
                return None
            conversations = connection.execute(
                "SELECT * FROM bootcamp_conversations WHERE run_id=? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        return {
            "id": run["id"],
            "status": run["status"],
            "selectedPersonaIds": json.loads(run["selected_personas_json"]),
            "maxTurns": run["max_turns"],
            "styleProfile": json.loads(run["style_profile_json"]),
            "error": run["error"],
            "createdAt": run["created_at"],
            "updatedAt": run["updated_at"],
            "conversations": [
                {
                    "id": row["id"],
                    "personaId": row["persona_id"],
                    "personaName": row["persona_name"],
                    "status": row["status"],
                    "currentTurn": row["current_turn"],
                    "needsHandoff": bool(row["needs_handoff"]),
                    "handoffReason": row["handoff_reason"],
                    "messages": self.get_conversation_messages(row["id"]),
                }
                for row in conversations
            ],
        }

    def latest_run(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM bootcamp_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self.get_run(row["id"]) if row else None

    def reset(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM bootcamp_runs")

    def count_messages(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM bootcamp_messages").fetchone()
        return int(row["count"])


class BootcampRunner:
    """Runs bounded persona threads using injected, side-effect-free generators."""

    def __init__(
        self,
        store: BootcampStore,
        openings: list[str],
        generate_tori: Callable[[list[dict[str, Any]], dict[str, int]], tuple[str, str | None]],
        generate_persona: Callable[[dict[str, str], list[dict[str, Any]], str | None], str],
        max_workers: int = 6,
        message_delay_seconds: float = 2.5,
    ):
        self.store = store
        self.openings = openings
        self.generate_tori = generate_tori
        self.generate_persona = generate_persona
        self.max_workers = max(1, min(6, max_workers))
        self.message_delay_seconds = max(0.0, min(10.0, float(message_delay_seconds)))
        self._threads: dict[str, threading.Thread] = {}
        self._pace_lock = threading.Lock()
        self._last_message_at = 0.0

    def _pace_message(self) -> None:
        """Globally stagger stored messages so polling clients receive calm updates."""
        with self._pace_lock:
            wait_for = self.message_delay_seconds - (time.monotonic() - self._last_message_at)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_message_at = time.monotonic()

    def start(self, persona_ids: list[str], max_turns: int, profile: dict[str, Any]) -> str:
        available = {persona["id"]: persona for persona in PERSONAS}
        selected = [available[item] for item in persona_ids if item in available]
        if not selected:
            raise ValueError("Select at least one valid persona")
        run_id = self.store.create_run([item["id"] for item in selected], max_turns, profile)
        thread = threading.Thread(
            target=self._run,
            args=(run_id, selected, max_turns, normalize_style_profile(profile)),
            daemon=True,
        )
        self._threads[run_id] = thread
        thread.start()
        return run_id

    def _run(
        self,
        run_id: str,
        personas: list[dict[str, str]],
        max_turns: int,
        profile: dict[str, int],
    ) -> None:
        try:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(personas))) as pool:
                futures = [pool.submit(self._run_persona, run_id, p, max_turns, profile) for p in personas]
                for future in futures:
                    future.result()
            if self.store.get_run_status(run_id) not in {"stopped", "reset"}:
                self.store.update_run(run_id, "completed")
        except Exception as exc:
            self.store.update_run(run_id, "failed", str(exc))

    def _wait_until_runnable(self, run_id: str) -> bool:
        while True:
            status = self.store.get_run_status(run_id)
            if status == "paused":
                time.sleep(0.4)
                continue
            return status == "running"

    def _run_persona(
        self,
        run_id: str,
        persona: dict[str, str],
        max_turns: int,
        profile: dict[str, int],
    ) -> None:
        conversation_id = self.store.create_conversation(run_id, persona["id"], persona["name"])
        seed = random.choice(self.openings) if self.openings else "Hi, can I ask about your services?"
        opening = self.generate_persona(persona, [], seed)
        self._pace_message()
        self.store.add_message(conversation_id, "persona", opening, {"source": "bootcamp-jsonl"})

        for turn in range(1, max(1, min(20, int(max_turns))) + 1):
            if not self._wait_until_runnable(run_id):
                self.store.update_conversation(conversation_id, status="stopped")
                return
            history = self.store.get_conversation_messages(conversation_id)
            tori_reply, handoff_reason = self.generate_tori(history, profile)
            previous_tori = next(
                (message["text"] for message in reversed(history) if message.get("role") == "tori"),
                None,
            )
            if (
                not handoff_reason
                and previous_tori
                and " ".join(tori_reply.casefold().split())
                == " ".join(previous_tori.casefold().split())
            ):
                tori_reply = ""
                handoff_reason = "Tori attempted to repeat the same reply"
            if tori_reply.strip():
                self._pace_message()
                self.store.add_message(conversation_id, "tori", tori_reply)
            self.store.update_conversation(conversation_id, current_turn=turn)
            if handoff_reason:
                self.store.update_conversation(
                    conversation_id,
                    status="handoff",
                    needs_handoff=True,
                    handoff_reason=handoff_reason,
                )
                return
            if turn >= max_turns:
                break
            history = self.store.get_conversation_messages(conversation_id)
            next_message = self.generate_persona(persona, history, None)
            self._pace_message()
            self.store.add_message(conversation_id, "persona", next_message)
        self.store.update_conversation(conversation_id, status="completed")

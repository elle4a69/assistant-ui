import asyncio
import json

import main


def test_lifespan_reloads_persisted_active_knowledge(tmp_path, monkeypatch):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / main.LEARNED_INFORMATION_FILENAME).write_text(
        json.dumps({
            "id": "persisted-rule",
            "text": "Use the established service policy.",
            "scope": "primary",
            "source_account_key": "primary",
            "status": "active",
            "review_status": "approved",
            "retrieval_enabled": True,
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "KNOWLEDGE_DIR", str(knowledge_dir))
    monkeypatch.setattr(main, "KNOWLEDGE_CHUNKS", [])
    monkeypatch.setattr(main.Base.metadata, "create_all", lambda **_kwargs: None)
    monkeypatch.setattr(main, "recover_interrupted_agent_console_runs", lambda: None)

    async def wait_for_cancellation():
        await asyncio.Future()

    monkeypatch.setattr(main, "arrival_alert_worker", wait_for_cancellation)
    monkeypatch.setattr(main, "start_agent_console_retention_worker", wait_for_cancellation)
    monkeypatch.setattr(main, "booking_reminder_worker", wait_for_cancellation)

    async def run_lifespan():
        async with main.lifespan(main.app):
            assert any(
                item["text"] == "Use the established service policy."
                for item in main.KNOWLEDGE_CHUNKS
            )

    asyncio.run(run_lifespan())

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

import main
from agent_console import (
    AgentConsoleError,
    AgentStep,
    parse_agent_arguments,
    sanitize_console_text,
    write_workspace_file,
)


class FakeParsedResponse:
    def __init__(self, step: AgentStep):
        message = type("Message", (), {"parsed": step})()
        self.choices = [type("Choice", (), {"message": message})()]


class FakeCompletions:
    def __init__(self, steps, delay=0):
        self.steps = list(steps)
        self.delay = delay
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            time.sleep(self.delay)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        return FakeParsedResponse(step)


class FakeOpenAIClient:
    def __init__(self, steps, delay=0):
        completions = FakeCompletions(steps, delay=delay)
        self.beta = type("Beta", (), {
            "chat": type("Chat", (), {"completions": completions})(),
        })()
        self.completions = completions
        self.options_calls = []

    def with_options(self, **kwargs):
        self.options_calls.append(kwargs)
        return self


@pytest.fixture
def isolated_agent_database(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent-console-test.db'}",
        connect_args={"check_same_thread": False},
    )
    main.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "AGENT_RUNS_DIR", tmp_path / "agent-runs")
    monkeypatch.setattr(main, "AUTH_PASSWORD", "agent-console-test-password")
    monkeypatch.setenv("OPS_AGENT_AUTONOMOUS_ENABLED", "true")
    monkeypatch.delenv("PUBLIC_APP_URL", raising=False)
    main._agent_run_tasks.clear()
    yield factory
    main._agent_run_tasks.clear()
    engine.dispose()


def authenticated_client() -> TestClient:
    client = TestClient(main.app)
    expires_at = int(datetime.now(timezone.utc).timestamp()) + 300
    client.cookies.set(main.AUTH_COOKIE_NAME, main._admin_session_token(expires_at))
    return client


def receive_until(socket, terminal_type: str):
    frames = []
    for _ in range(30):
        frame = socket.receive_json()
        frames.append(frame)
        if frame.get("type") == terminal_type:
            return frames
    raise AssertionError(f"Did not receive {terminal_type}: {frames}")


def test_agent_step_enforces_structured_allowlist_and_json_object():
    step = AgentStep(
        thought="Inspecting the current main branch.",
        action="read_file",
        arguments='{"path":"backend/main.py"}',
    )
    assert step.action == "read_file"
    assert parse_agent_arguments(step.arguments)["path"] == "backend/main.py"

    with pytest.raises(ValueError):
        AgentStep(thought="Run it", action="shell", arguments="{}")
    with pytest.raises(AgentConsoleError):
        parse_agent_arguments('["not", "an", "object"]')


def test_console_sanitization_removes_secrets_and_terminal_controls():
    clean = sanitize_console_text(
        'token=private-value {"token":"json-token","password": "json-password"}'
        "\x1b]52;c;clipboard\x07\x1b[31mred\x1b[0m\u009b31m"
    )
    assert "private-value" not in clean
    assert "json-token" not in clean
    assert "json-password" not in clean
    assert "[REDACTED]" in clean
    assert "\x1b" not in clean
    assert "\u009b" not in clean
    assert "clipboard" not in clean


def test_console_sanitization_recursively_redacts_structured_secret_values():
    escaped = sanitize_console_text(r'{"token":"abc\"def-SECRET-TAIL"}')
    nested = sanitize_console_text(
        '{"token":{"value":"NESTED-SECRET-TAIL"},"api_key":["ARRAY-SECRET-TAIL"]}'
    )

    assert "SECRET-TAIL" not in escaped
    assert "NESTED-SECRET-TAIL" not in nested
    assert "ARRAY-SECRET-TAIL" not in nested
    assert json.loads(escaped)["token"] == "[REDACTED]"
    parsed_nested = json.loads(nested)
    assert parsed_nested["token"] == "[REDACTED]"
    assert parsed_nested["api_key"] == "[REDACTED]"

    prefixed = sanitize_console_text(
        'OPS_AGENT_BEARER_TOKEN=opaque-value-123 MY_API_KEY=other-value '
        'DATABASE_PASSWORD=database-value '
        '{"OPS_AGENT_BEARER_TOKEN":"json-prefixed-value"}'
    )
    assert "opaque-value-123" not in prefixed
    assert "other-value" not in prefixed
    assert "database-value" not in prefixed
    assert "json-prefixed-value" not in prefixed

    headers = sanitize_console_text({
        "headers": {
            "Authorization": "Bearer opaque-auth-value",
            "Proxy-Authorization": "Basic opaque-proxy-value",
            "Cookie": "session=opaque-cookie-value",
            "Set-Cookie": "session=opaque-set-cookie-value",
        }
    })
    assert "opaque-auth-value" not in headers
    assert "opaque-proxy-value" not in headers
    assert "opaque-cookie-value" not in headers
    assert "opaque-set-cookie-value" not in headers

    raw_headers = sanitize_console_text(
        "Authorization: Bearer TEST-BEARER-VALUE-456\n"
        "authorization: Basic TEST-BASIC-VALUE-456\n"
        "Proxy-Authorization: Basic TEST-PROXY-VALUE-456\n"
        "Cookie: session=TEST-RAW-COOKIE-VALUE-789\n"
        "Set-Cookie: session=TEST-SET-COOKIE-VALUE-789; HttpOnly\n"
        "assistant_ui_admin_session=TEST-COOKIE-VALUE-789"
    )
    for leaked_value in (
        "TEST-BEARER-VALUE-456",
        "TEST-BASIC-VALUE-456",
        "TEST-PROXY-VALUE-456",
        "TEST-RAW-COOKIE-VALUE-789",
        "TEST-SET-COOKIE-VALUE-789",
        "TEST-COOKIE-VALUE-789",
    ):
        assert leaked_value not in raw_headers


def test_scratch_write_is_bounded_redacted_and_cannot_traverse(tmp_path):
    workspace = tmp_path / "run" / "workspace"
    result = write_workspace_file(workspace, "notes/result.md", "api_key=private-value\nChecked.")
    saved = (workspace / "notes" / "result.md").read_text(encoding="utf-8")
    assert result["scope"] == "isolated scratch workspace"
    assert "private-value" not in saved
    assert "[REDACTED]" in saved

    with pytest.raises(AgentConsoleError):
        write_workspace_file(workspace, "../assistant.db", "bad")
    with pytest.raises(AgentConsoleError):
        write_workspace_file(workspace, "/data/assistant.db", "bad")
    with pytest.raises(AgentConsoleError):
        write_workspace_file(workspace, "notes/executable.exe", "bad")

    quota_root = tmp_path / "quota-root"
    existing_workspace = quota_root / "existing-run" / "workspace"
    existing_workspace.mkdir(parents=True)
    (existing_workspace / "used.txt").write_text("full", encoding="utf-8")
    with pytest.raises(AgentConsoleError, match="global limit"):
        write_workspace_file(
            quota_root / "new-run" / "workspace",
            "notes/result.md",
            "more",
            global_root=quota_root,
            max_global_bytes=4,
        )


def test_virtual_terminal_rejects_raw_shell_commands(isolated_agent_database):
    with pytest.raises(AgentConsoleError):
        main._agent_execute_action(
            "run-id",
            "run_terminal_command",
            json.dumps({"command": "rm -rf /"}),
            "Inspect the service",
        )


def test_autonomous_virtual_tools_are_explicitly_scoped():
    assert "inspect_system_status" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "start_coding_task" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "remember_operational_learning" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "propose_runtime_change" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "execute_runtime_change" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "propose_code_deployment" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "execute_code_deployment" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "research_internet" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "inspect_deployments" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "inspect_coding_task" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "inspect_code_changes" in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "run_shell" not in main.AGENT_CONSOLE_ALLOWED_TOOLS
    assert "start_coding_task" in main.AGENT_CONSOLE_CRITICAL_TOOLS
    assert "execute_code_deployment" in main.AGENT_CONSOLE_CRITICAL_TOOLS
    assert "execute_runtime_change" in main.AGENT_CONSOLE_CRITICAL_TOOLS


def test_agent_prompt_forbids_customer_evidence_in_coding_task_fields():
    prompt = main.build_agent_system_prompt(
        "[]",
        15,
        conversation_context="USER: The earlier repair still fails.",
        durable_memory='[{"title":"Keep replies concise"}]',
    )

    assert "anonymised engineering defect" in prompt
    assert "Never put a customer name" in prompt
    assert "verbatim/paraphrased customer message" in prompt
    assert "Do not copy an inspect_conversation transcript" in prompt
    assert "conversational coding agent" in prompt
    assert "The earlier repair still fails" in prompt
    assert "Keep replies concise" in prompt
    assert "Only the current owner message" in prompt


def test_agent_run_persists_one_idempotent_owner_chat_turn(isolated_agent_database):
    request_id = "abababab-abab-4bab-8bab-abababababab"
    first, created = main._create_agent_run(request_id, "Please repair the failing simulator.")
    duplicate, duplicate_created = main._create_agent_run(
        request_id,
        "Please repair the failing simulator.",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == first.id

    db = isolated_agent_database()
    try:
        messages = db.query(main.OperationsChatMessage).all()
        assert len(messages) == 1
        assert messages[0].id == main._agent_console_chat_message_id(first.id, "user")
        assert messages[0].role == "user"
        assert messages[0].content == "Please repair the failing simulator."
    finally:
        db.close()


def test_agent_completion_is_saved_as_the_conversational_reply(isolated_agent_database):
    run, _created = main._create_agent_run(
        "acacacac-acac-4cac-8cac-acacacacacac",
        "Check the repair and report back.",
    )
    main._finish_agent_run(
        run.id,
        "completed",
        "The checks passed.",
        event_type="completed",
        summary="The checks passed and the repair is ready.",
    )

    db = isolated_agent_database()
    try:
        messages = db.query(main.OperationsChatMessage).order_by(
            main.OperationsChatMessage.created_at,
            main.OperationsChatMessage.id,
        ).all()
        assert [(item.role, item.content) for item in messages] == [
            ("user", "Check the repair and report back."),
            ("assistant", "The checks passed and the repair is ready."),
        ]
        assert messages[-1].id == main._agent_console_chat_message_id(run.id, "assistant")
    finally:
        db.close()


def test_follow_up_automatically_receives_prior_chat_and_durable_memory(
    isolated_agent_database,
    monkeypatch,
):
    db = isolated_agent_database()
    try:
        db.add_all([
            main.OperationsChatMessage(
                id="earlier-owner-turn",
                role="user",
                content="The booking alert is repeating.",
            ),
            main.OperationsChatMessage(
                id="earlier-agent-turn",
                role="assistant",
                content="I repaired the alert deduplication and verified it.",
            ),
            main.OperationsMemory(
                category="preference",
                title="Keep reports direct",
                content="Lead with the outcome and avoid unnecessary implementation detail.",
                evidence="Explicit owner preference.",
            ),
        ])
        db.commit()
    finally:
        db.close()

    fake_client = FakeOpenAIClient([
        AgentStep(
            thought="I checked the follow-up against the earlier repair.",
            action="complete",
            arguments='{"summary":"I retained the earlier context and handled the follow-up."}',
        ),
    ])
    monkeypatch.setattr(main, "openai_client", fake_client)
    run, _created = main._create_agent_run(
        "adadadad-adad-4dad-8dad-adadadadadad",
        "It is still not working.",
    )

    asyncio.run(main._run_agent_console(run.id, run.objective, 1))

    model_messages = fake_client.completions.calls[0]["messages"]
    assert "The booking alert is repeating" in model_messages[0]["content"]
    assert "I repaired the alert deduplication" in model_messages[0]["content"]
    assert "Keep reports direct" in model_messages[0]["content"]
    assert "It is still not working" not in model_messages[0]["content"]
    assert model_messages[1] == {
        "role": "user",
        "content": "Current owner message:\nIt is still not working.",
    }


def test_conversation_context_keeps_newest_turns_within_budget(isolated_agent_database):
    db = isolated_agent_database()
    try:
        db.add(main.OperationsChatMessage(
            id="old-context",
            role="user",
            content="OLDEST-CONTEXT " + ("old " * 500),
            created_at=datetime.utcnow() - timedelta(days=2),
        ))
        db.add(main.OperationsChatMessage(
            id="new-context",
            role="assistant",
            content="NEWEST-CONTEXT should survive the bounded retrieval.",
            created_at=datetime.utcnow() - timedelta(minutes=1),
        ))
        db.commit()
        context = main._build_agent_conversation_context(
            db,
            current_run_id="aeaeaeae-aeae-4eae-8eae-aeaeaeaeaeae",
            max_chars=700,
        )
    finally:
        db.close()

    assert len(context) <= 700
    assert "NEWEST-CONTEXT" in context
    assert "OLDEST-CONTEXT" not in context


def test_conversation_history_returns_the_latest_two_hundred_in_order(isolated_agent_database):
    db = isolated_agent_database()
    try:
        started_at = datetime.utcnow() - timedelta(hours=1)
        db.add_all([
            main.OperationsChatMessage(
                id=f"history-{index:03d}",
                role="user" if index % 2 == 0 else "assistant",
                content=f"Message {index}",
                created_at=started_at + timedelta(seconds=index),
            )
            for index in range(205)
        ])
        db.commit()
        result = main.get_operations_chat_messages(db)
    finally:
        db.close()

    assert len(result["messages"]) == 200
    assert result["messages"][0]["content"] == "Message 5"
    assert result["messages"][-1]["content"] == "Message 204"


def test_one_autonomous_run_cannot_submit_duplicate_coding_tasks(
    isolated_agent_database,
    monkeypatch,
):
    run, _created = main._create_agent_run(
        "99999999-9999-4999-8999-999999999999",
        "Implement one isolated review-branch fix.",
    )
    main._agent_record_running(run.id)
    main._agent_record_observation(
        run.id,
        1,
        "ops start_coding_task",
        '{"status":"started","task_id":"task-one"}',
        "stdout",
    )

    def unexpected_execution(*_args, **_kwargs):
        raise AssertionError("duplicate coding submission reached the wider tool executor")

    monkeypatch.setattr(main, "execute_operations_tool", unexpected_execution)
    _label, observation, stream = main._agent_execute_action(
        run.id,
        "run_terminal_command",
        json.dumps({
            "tool": "start_coding_task",
            "arguments": {
                "title": "Duplicate task",
                "instructions": "Do the same isolated implementation again.",
                "acceptance_test": "All tests pass.",
            },
        }),
        run.objective,
    )
    assert json.loads(observation)["status"] == "rejected"
    assert stream == "stderr"


def test_coding_task_rejects_private_or_control_data_and_honours_old_active_work(
    isolated_agent_database,
    monkeypatch,
):
    monkeypatch.setattr(main, "operations_code_access_available", lambda: True)
    valid = {
        "title": "Repair an isolated code path",
        "instructions": "Inspect the relevant source and implement a tested review-branch repair.",
        "acceptance_test": "The focused tests pass.",
    }
    invalid_variants = [
        {**valid, "title": "Bad\nTitle"},
        {**valid, "instructions": "Investigate the customer conversation for 0432172148 safely."},
        {**valid, "instructions": "Investigate the account owned by customer@example.com safely."},
        {**valid, "instructions": "Use FLY_API_TOKEN=do-not-copy-this-value in the repair."},
        {**valid, "acceptance_test": "No NUL\x00is accepted."},
    ]

    db = isolated_agent_database()
    try:
        for arguments in invalid_variants:
            result = main._operations_start_coding_task(db, **arguments)
            assert result["status"] == "rejected"
        assert db.query(main.OperationsAction).count() == 0

        old_active = main.OperationsAction(
            action_type="coding_task",
            payload=json.dumps({"title": "Existing queued repair"}),
            reason="Existing work must remain globally deduplicated.",
            status="queued",
            created_at=datetime.utcnow() - timedelta(days=3),
        )
        db.add(old_active)
        db.commit()

        result = main._operations_start_coding_task(db, **valid)
        assert result["status"] == "already_running"
        assert result["task_id"] == old_active.id
        assert db.query(main.OperationsAction).count() == 1
    finally:
        db.close()


def test_agent_coding_submission_does_not_change_the_shared_sqlite_busy_timeout(
    isolated_agent_database,
    monkeypatch,
):
    monkeypatch.setattr(main, "operations_code_access_available", lambda: True)
    db = isolated_agent_database()
    try:
        db.connection().exec_driver_sql("PRAGMA busy_timeout=30000")
    finally:
        db.close()

    _label, observation, stream = main._agent_execute_action(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "run_terminal_command",
        json.dumps({
            "tool": "start_coding_task",
            "arguments": {
                "title": "Repair one isolated code path",
                "instructions": "Inspect the relevant source and implement a tested review-branch repair.",
                "acceptance_test": "The focused tests pass.",
            },
        }),
        "Implement a safe isolated repair.",
    )

    result = json.loads(observation)
    assert result["status"] == "started"
    assert stream == "stdout"
    db = isolated_agent_database()
    try:
        assert db.connection().exec_driver_sql("PRAGMA busy_timeout").scalar() == 30_000
        action = db.query(main.OperationsAction).one()
        assert action.id == result["task_id"]
        assert json.loads(action.payload)["origin_agent_run_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    finally:
        db.close()


def test_coding_submission_is_not_started_without_its_reserved_deadline(
    isolated_agent_database,
    monkeypatch,
):
    monkeypatch.setattr(main, "agent_console_total_timeout_seconds", lambda: 0.5)
    monkeypatch.setattr(main, "openai_client", FakeOpenAIClient([
        AgentStep(
            thought="Preparing one isolated review-branch repair.",
            action="run_terminal_command",
            arguments=json.dumps({
                "tool": "start_coding_task",
                "arguments": {
                    "title": "Repair one isolated code path",
                    "instructions": "Inspect the relevant source and implement a tested review-branch repair.",
                    "acceptance_test": "The focused tests pass.",
                },
            }),
        ),
    ]))

    def unexpected_submission(*_args, **_kwargs):
        raise AssertionError("a coding task started without enough bounded time to audit it")

    monkeypatch.setattr(main, "_agent_execute_action", unexpected_submission)
    run, _created = main._create_agent_run(
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "Implement one isolated review-branch repair.",
    )
    asyncio.run(main._run_agent_console(run.id, run.objective, 1))

    db = isolated_agent_database()
    try:
        persisted = db.query(main.OperationsAgentRun).filter(main.OperationsAgentRun.id == run.id).one()
        assert persisted.status == "failed"
        assert persisted.error == "Execution timeout"
        assert db.query(main.OperationsAction).count() == 0
    finally:
        db.close()


def test_process_cancellation_waits_for_coding_submission_audit(
    isolated_agent_database,
    monkeypatch,
):
    submission_started = threading.Event()
    release_submission = threading.Event()

    def delayed_submission(*_args, **_kwargs):
        submission_started.set()
        assert release_submission.wait(2)
        return (
            "ops start_coding_task",
            '{"status":"started","task_id":"task-after-cancel"}',
            "stdout",
        )

    monkeypatch.setattr(main, "_agent_execute_action", delayed_submission)
    monkeypatch.setattr(main, "openai_client", FakeOpenAIClient([
        AgentStep(
            thought="Submitting one isolated review-branch repair.",
            action="run_terminal_command",
            arguments=json.dumps({
                "tool": "start_coding_task",
                "arguments": {
                    "title": "Repair one isolated code path",
                    "instructions": "Inspect the relevant source and implement a tested review-branch repair.",
                    "acceptance_test": "The focused tests pass.",
                },
            }),
        ),
    ]))
    run, _created = main._create_agent_run(
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "Implement one isolated review-branch repair.",
    )

    async def exercise_process_cancellation():
        task = asyncio.create_task(main._run_agent_console(run.id, run.objective, 1))
        assert await asyncio.to_thread(submission_started.wait, 1)
        task.cancel()
        release_submission.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise_process_cancellation())

    db = isolated_agent_database()
    try:
        persisted = db.query(main.OperationsAgentRun).filter(main.OperationsAgentRun.id == run.id).one()
        events = db.query(main.OperationsAgentEvent).filter(
            main.OperationsAgentEvent.run_id == run.id
        ).order_by(main.OperationsAgentEvent.sequence).all()
        terminal_index = next(
            index for index, event in enumerate(events)
            if event.event_type == "terminal" and "task-after-cancel" in event.message
        )
        error_index = next(index for index, event in enumerate(events) if event.event_type == "error")
        assert terminal_index < error_index
        assert persisted.status == "interrupted"
    finally:
        db.close()


def test_duplicate_start_is_idempotent_without_interrupting_live_run(isolated_agent_database):
    request_id = "55555555-5555-4555-8555-555555555555"
    first, created = main._create_agent_run(request_id, "Inspect the current system safely.")
    duplicate, duplicate_created = main._create_agent_run(
        request_id,
        "Inspect the current system safely.",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
    assert duplicate.status == "starting"

    db = isolated_agent_database()
    try:
        persisted = db.query(main.OperationsAgentRun).filter(
            main.OperationsAgentRun.id == first.id
        ).one()
        events = db.query(main.OperationsAgentEvent).filter(
            main.OperationsAgentEvent.run_id == first.id
        ).all()
        assert persisted.status == "starting"
        assert [event.event_type for event in events] == ["run_started"]
    finally:
        db.close()


def test_history_and_global_scratch_retention_are_bounded(
    isolated_agent_database,
    monkeypatch,
):
    monkeypatch.setattr(main, "AGENT_CONSOLE_HISTORY_LIMIT", 2)
    monkeypatch.setattr(main, "AGENT_CONSOLE_WORKSPACE_LIMIT_BYTES", 5)
    now = datetime.utcnow()
    run_ids = []
    db = isolated_agent_database()
    try:
        for index in range(3):
            run = main.OperationsAgentRun(
                request_id=str(uuid.UUID(int=index + 1)),
                actor="admin",
                objective=f"Completed run {index}",
                status="completed",
                max_steps=1,
                updated_at=now - timedelta(minutes=index),
                completed_at=now - timedelta(minutes=index),
            )
            db.add(run)
            db.flush()
            run_ids.append(run.id)
            db.add(main.OperationsAgentEvent(
                run_id=run.id,
                sequence=1,
                event_type="completed",
                message="done",
            ))
        db.commit()
        for run_id in run_ids:
            workspace = main.AGENT_RUNS_DIR / run_id / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "note.txt").write_text("four", encoding="utf-8")

        main._prune_agent_console_history(db)

        retained = db.query(main.OperationsAgentRun).order_by(
            main.OperationsAgentRun.updated_at.desc()
        ).all()
        assert [run.id for run in retained] == run_ids[:2]
        assert db.query(main.OperationsAgentEvent).count() == 2
        assert (main.AGENT_RUNS_DIR / run_ids[0]).exists()
        assert not (main.AGENT_RUNS_DIR / run_ids[1]).exists()
        assert not (main.AGENT_RUNS_DIR / run_ids[2]).exists()
    finally:
        db.close()


def test_normal_completion_enforces_history_limit(isolated_agent_database, monkeypatch):
    monkeypatch.setattr(main, "AGENT_CONSOLE_HISTORY_LIMIT", 2)
    for index in range(3):
        run, created = main._create_agent_run(
            str(uuid.UUID(int=100 + index)),
            f"Complete lifecycle {index}",
        )
        assert created is True
        main._finish_agent_run(
            run.id,
            "completed",
            "done",
            event_type="completed",
            summary="done",
        )

    db = isolated_agent_database()
    try:
        assert db.query(main.OperationsAgentRun).count() == 2
        assert db.query(main.OperationsAgentEvent).count() == 4
    finally:
        db.close()


def test_cancel_and_completion_never_append_after_terminal_event(
    isolated_agent_database,
    monkeypatch,
):
    run, _created = main._create_agent_run(
        "66666666-6666-4666-8666-666666666666",
        "Exercise the terminal transition race.",
    )
    main._agent_record_running(run.id)
    cancel_entered = threading.Event()
    release_cancel = threading.Event()
    original_append = main._append_agent_event

    def delayed_append(db, current_run, event_type, message, **kwargs):
        if kwargs.get("meta", {}).get("status") == "cancelling":
            cancel_entered.set()
            assert release_cancel.wait(2)
        return original_append(db, current_run, event_type, message, **kwargs)

    monkeypatch.setattr(main, "_append_agent_event", delayed_append)
    cancel_thread = threading.Thread(target=main._request_agent_cancel, args=(run.id,))
    finish_thread = threading.Thread(
        target=main._finish_agent_run,
        args=(run.id, "completed", "done"),
        kwargs={"event_type": "completed", "summary": "done"},
    )
    cancel_thread.start()
    assert cancel_entered.wait(1)
    finish_thread.start()
    time.sleep(0.05)
    assert finish_thread.is_alive()
    release_cancel.set()
    cancel_thread.join(2)
    finish_thread.join(2)
    assert not cancel_thread.is_alive()
    assert not finish_thread.is_alive()

    db = isolated_agent_database()
    try:
        persisted = db.query(main.OperationsAgentRun).filter(
            main.OperationsAgentRun.id == run.id
        ).one()
        events = db.query(main.OperationsAgentEvent).filter(
            main.OperationsAgentEvent.run_id == run.id
        ).order_by(main.OperationsAgentEvent.sequence).all()
        assert persisted.status == "completed"
        assert events[-1].event_type == "completed"
        assert sum(event.event_type in {"completed", "cancelled", "error", "limit_reached"} for event in events) == 1
    finally:
        db.close()


def test_timed_out_action_cannot_starve_default_executor(
    isolated_agent_database,
    monkeypatch,
):
    blocked = threading.Event()
    release = threading.Event()

    def blocked_read(*_args):
        blocked.set()
        release.wait(2)
        return "read", '{"status":"ok"}', "stdout"

    monkeypatch.setattr(main, "_agent_execute_action", blocked_read)
    monkeypatch.setattr(main, "AGENT_CONSOLE_ACTION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(main, "openai_client", FakeOpenAIClient([
        AgentStep(
            thought="Attempting one bounded inspection.",
            action="read_file",
            arguments='{"scope":"workspace","path":"notes/test.md"}',
        ),
    ]))
    run, _created = main._create_agent_run(
        "77777777-7777-4777-8777-777777777777",
        "Verify action timeout isolation.",
    )

    async def exercise_timeout():
        task = asyncio.create_task(main._run_agent_console(run.id, run.objective, 1))
        assert await asyncio.to_thread(blocked.wait, 1)
        await asyncio.wait_for(task, timeout=2)
        return await asyncio.wait_for(asyncio.to_thread(lambda: "responsive"), timeout=0.5)

    try:
        assert asyncio.run(exercise_timeout()) == "responsive"
        db = isolated_agent_database()
        try:
            persisted = db.query(main.OperationsAgentRun).filter(
                main.OperationsAgentRun.id == run.id
            ).one()
            assert persisted.status == "failed"
            assert persisted.error == "Execution timeout"
        finally:
            db.close()
    finally:
        release.set()


def test_total_deadline_stops_a_blocked_model_without_starving_default_executor(
    isolated_agent_database,
    monkeypatch,
):
    blocked = threading.Event()
    release = threading.Event()

    def blocked_model(_messages, _timeout_seconds=30):
        blocked.set()
        release.wait(2)
        return AgentStep(thought="late", action="complete", arguments='{"summary":"late"}')

    monkeypatch.setattr(main, "_agent_model_step", blocked_model)
    monkeypatch.setattr(main, "agent_console_total_timeout_seconds", lambda: 0.05)
    run, _created = main._create_agent_run(
        "88888888-8888-4888-8888-888888888888",
        "Verify the total run deadline.",
    )

    async def exercise_timeout():
        task = asyncio.create_task(main._run_agent_console(run.id, run.objective, 1))
        assert await asyncio.to_thread(blocked.wait, 1)
        await asyncio.wait_for(task, timeout=2)
        return await asyncio.wait_for(asyncio.to_thread(lambda: "responsive"), timeout=0.5)

    try:
        assert asyncio.run(exercise_timeout()) == "responsive"
        db = isolated_agent_database()
        try:
            persisted = db.query(main.OperationsAgentRun).filter(
                main.OperationsAgentRun.id == run.id
            ).one()
            assert persisted.status == "failed"
            assert persisted.error == "Execution timeout"
        finally:
            db.close()
    finally:
        release.set()


def test_orphaned_active_run_is_marked_interrupted(isolated_agent_database):
    db = isolated_agent_database()
    try:
        run = main.OperationsAgentRun(
            request_id="44444444-4444-4444-8444-444444444444",
            actor="admin",
            objective="A run interrupted by a process restart.",
            status="running",
            max_steps=15,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    main._interrupt_agent_run_if_orphaned(run_id)

    db = isolated_agent_database()
    try:
        recovered = db.query(main.OperationsAgentRun).filter(main.OperationsAgentRun.id == run_id).one()
        event = db.query(main.OperationsAgentEvent).filter(main.OperationsAgentEvent.run_id == run_id).one()
        chat = db.query(main.OperationsChatMessage).order_by(
            main.OperationsChatMessage.created_at,
            main.OperationsChatMessage.id,
        ).all()
        assert recovered.status == "interrupted"
        assert event.event_type == "error"
        assert json.loads(event.meta)["code"] == "server_restarted"
        assert [(item.role, item.content) for item in chat] == [
            ("user", "A run interrupted by a process restart."),
            ("assistant", "The web process restarted before this orchestration run finished."),
        ]
    finally:
        db.close()


def test_websocket_fails_closed_without_admin_authentication(monkeypatch):
    monkeypatch.setattr(main, "AUTH_PASSWORD", "")
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws/agent", headers={"origin": "http://testserver"}):
            pass
    assert rejected.value.code == 4401


def test_websocket_rejects_cross_origin(isolated_agent_database, monkeypatch):
    monkeypatch.setattr(main, "openai_client", FakeOpenAIClient([
        AgentStep(thought="Done.", action="complete", arguments='{"summary":"Done."}'),
    ]))
    client = authenticated_client()
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws/agent", headers={"origin": "https://attacker.example"}):
            pass
    assert rejected.value.code == 4403


def test_websocket_rejects_same_host_with_wrong_origin_scheme(isolated_agent_database, monkeypatch):
    monkeypatch.setattr(main, "openai_client", FakeOpenAIClient([
        AgentStep(thought="Done.", action="complete", arguments='{"summary":"Done."}'),
    ]))
    client = authenticated_client()
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws/agent", headers={"origin": "https://testserver"}):
            pass
    assert rejected.value.code == 4403


def test_authenticated_websocket_streams_ordered_persisted_completion(
    isolated_agent_database,
    monkeypatch,
):
    fake_client = FakeOpenAIClient([
        AgentStep(
            thought="The requested inspection is complete.",
            action="complete",
            arguments='{"summary":"Verified without changing production."}',
        ),
    ])
    monkeypatch.setattr(main, "openai_client", fake_client)
    client = authenticated_client()
    request_id = "11111111-1111-4111-8111-111111111111"

    with client.websocket_connect("/ws/agent", headers={"origin": "http://testserver"}) as socket:
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        assert ready["limits"]["maxSteps"] == 15
        socket.send_json({"type": "start", "requestId": request_id, "objective": "Inspect safely and finish."})
        frames = receive_until(socket, "completed")

    persisted = [frame for frame in frames if "sequence" in frame]
    assert [frame["sequence"] for frame in persisted] == list(range(1, len(persisted) + 1))
    assert [frame["type"] for frame in persisted] == ["run_started", "status", "status", "completed"]
    assert persisted[-1]["summary"] == "Verified without changing production."
    assert fake_client.completions.calls[0]["response_format"] is AgentStep
    assert fake_client.completions.calls[0]["store"] is False
    assert fake_client.completions.calls[0]["timeout"] == 30
    assert fake_client.options_calls[0]["max_retries"] == 0
    assert "private reasoning" in fake_client.completions.calls[0]["messages"][0]["content"]

    db = isolated_agent_database()
    try:
        run = db.query(main.OperationsAgentRun).one()
        events = db.query(main.OperationsAgentEvent).order_by(main.OperationsAgentEvent.sequence).all()
        assert run.status == "completed"
        assert run.step_count == 1
        assert len(events) == 4
    finally:
        db.close()


def test_websocket_cancel_is_idempotent_and_replayable(isolated_agent_database, monkeypatch):
    repeated_step = AgentStep(
        thought="Checking the isolated scratch workspace.",
        action="read_file",
        arguments='{"scope":"workspace","path":"notes/missing.md"}',
    )
    monkeypatch.setattr(main, "openai_client", FakeOpenAIClient([repeated_step], delay=0.1))
    monkeypatch.setenv("OPS_AGENT_MAX_STEPS", "5")
    client = authenticated_client()

    with client.websocket_connect("/ws/agent", headers={"origin": "http://testserver"}) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({
            "type": "start",
            "requestId": "22222222-2222-4222-8222-222222222222",
            "objective": "Keep checking until cancelled.",
        })
        first_frames = []
        run_id = None
        for _ in range(20):
            frame = socket.receive_json()
            first_frames.append(frame)
            run_id = frame.get("runId") or run_id
            if frame.get("type") == "terminal":
                break
        assert run_id
        socket.send_json({"type": "cancel", "runId": run_id})
        socket.send_json({"type": "cancel", "runId": run_id})
        cancelled_frames = receive_until(socket, "cancelled")
        assert any(frame.get("status") == "cancelling" for frame in cancelled_frames)

    with client.websocket_connect("/ws/agent", headers={"origin": "http://testserver"}) as replay:
        assert replay.receive_json()["type"] == "ready"
        replay.send_json({"type": "attach", "runId": run_id, "afterSequence": 0})
        replayed = receive_until(replay, "cancelled")
    sequences = [frame["sequence"] for frame in replayed if "sequence" in frame]
    assert sequences == sorted(set(sequences))


def test_step_cap_finishes_without_an_infinite_loop(isolated_agent_database, monkeypatch):
    repeated_step = AgentStep(
        thought="One bounded check was attempted.",
        action="read_file",
        arguments='{"scope":"workspace","path":"notes/missing.md"}',
    )
    monkeypatch.setattr(main, "openai_client", FakeOpenAIClient([repeated_step]))
    monkeypatch.setenv("OPS_AGENT_MAX_STEPS", "2")
    client = authenticated_client()

    with client.websocket_connect("/ws/agent", headers={"origin": "http://testserver"}) as socket:
        socket.receive_json()
        socket.send_json({
            "type": "start",
            "requestId": "33333333-3333-4333-8333-333333333333",
            "objective": "Attempt a bounded check.",
        })
        frames = receive_until(socket, "limit_reached")
    assert frames[-1]["status"] == "step_limit"
    assert frames[-1]["steps"] == 2

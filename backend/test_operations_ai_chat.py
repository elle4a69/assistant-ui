from datetime import datetime, timedelta
from urllib import request as url_request

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, Message, OperationsChatInput, OperationsChatMessage, OperationsMemory, Thread, ThreadEvent


class FakeResponses:
    def __init__(self, output_text: str):
        self.output_text = output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output_text": self.output_text})()


class FakeOpenAIClient:
    def __init__(self, output_text: str):
        self.responses = FakeResponses(output_text)


class FakeFunctionCall:
    type = "function_call"

    def __init__(self, name: str, arguments: str, call_id: str):
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class SequenceResponses:
    def __init__(self, responses):
        self.pending = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.pending.pop(0)


class FakeHTTPResponse:
    def __init__(self, body: str):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body.encode("utf-8")


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_operations_chat_persists_both_sides_and_uses_read_only_snapshot(monkeypatch):
    client = FakeOpenAIClient("That is not established by the current snapshot.")
    monkeypatch.setattr(main, "openai_client", client)
    monkeypatch.setattr(main, "load_booking_services", lambda: [{
        "id": "service-1",
        "name": "Consult",
        "duration": 30,
        "price": 100,
    }])
    monkeypatch.setattr(main, "load_working_hours", lambda: [])
    db = make_db()
    db.add(Thread(
        id="operations-thread",
        customer_phone="+61412345678",
        state="needs-review",
        priority="medium",
        sla_due_at=datetime.utcnow() + timedelta(hours=1),
        unread_count=0,
    ))
    db.commit()

    result = main.send_operations_chat_message(
        OperationsChatInput(message="Why did that happen?"),
        db,
    )

    assert result["assistantMessage"]["content"] == "That is not established by the current snapshot."
    assert result["capabilities"] == {
        "readOnly": False,
        "liveSnapshot": True,
        "codeAccess": False,
        "logAccess": True,
        "diagnosticTools": True,
        "messageSelfDiagnosis": True,
        "webSearch": True,
        "persistentMemory": True,
        "controlledActions": True,
        "requiresConfirmation": True,
    }
    assert [item.role for item in db.query(OperationsChatMessage).all()] == ["user", "assistant"]
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-terra"
    assert call["store"] is False
    assert '"needs_review_count": 1' in call["instructions"]
    assert "cannot edit source code" in call["instructions"]
    assert "Lead every response with the outcome" in call["instructions"]
    assert "Do not use corporate, bureaucratic or academic phrasing" in call["instructions"]
    assert call["tools"] == main.OPERATIONS_AI_TOOLS
    assert call["max_output_tokens"] == 1200
    assert "include" not in call
    owner_style = db.query(OperationsMemory).filter(
        OperationsMemory.title == main.OPERATIONS_OWNER_WORKING_STYLE_TITLE
    ).one()
    assert "complete and verify the work" in owner_style.content
    db.close()


def test_operations_runtime_change_requires_exact_separate_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)
    db = make_db()

    proposed = main.execute_operations_tool(
        db,
        "propose_runtime_change",
        {"action": "pause_customer_ai", "reason": "Unexpected replies are being investigated."},
        "Please pause it",
    )

    assert proposed["status"] == "pending_confirmation"
    assert main.AUTO_REPLY_GLOBAL_ENABLED is True
    rejected = main.execute_operations_tool(
        db,
        "execute_runtime_change",
        {"action_id": proposed["action_id"]},
        "yes do it",
    )
    assert rejected["status"] == "rejected"
    assert main.AUTO_REPLY_GLOBAL_ENABLED is True

    executed = main.execute_operations_tool(
        db,
        "execute_runtime_change",
        {"action_id": proposed["action_id"]},
        proposed["confirmation_phrase"],
    )

    assert executed["status"] == "executed"
    assert main.AUTO_REPLY_GLOBAL_ENABLED is False
    assert (tmp_path / "auto_reply_global.json").read_text(encoding="utf-8") == '{\n  "enabled": false\n}'
    repeated = main.execute_operations_tool(
        db,
        "execute_runtime_change",
        {"action_id": proposed["action_id"]},
        proposed["confirmation_phrase"],
    )
    assert repeated["status"] == "rejected"
    db.close()


def test_operations_runtime_change_can_update_message_ui_and_account_responder(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MESSAGE_UI_SETTINGS_PATH", str(tmp_path / "message_ui_settings.json"))
    responders = {
        "primary": {"enabled": True, "cooldownDays": 1, "delaySeconds": 5, "message": "Tori"},
        "secondary": {"enabled": True, "cooldownDays": 1, "delaySeconds": 5, "message": "Anonymous"},
    }
    monkeypatch.setattr(main, "load_first_contact_autoresponders", lambda: responders)
    monkeypatch.setattr(main, "save_first_contact_autoresponders", lambda value: responders.update(value))
    db = make_db()

    avatars = main.execute_operations_tool(
        db,
        "propose_runtime_change",
        {"action": "hide_message_avatars", "reason": "Use the compact message display."},
        "Hide them",
    )
    avatar_result = main.execute_operations_tool(
        db, "execute_runtime_change", {"action_id": avatars["action_id"]}, avatars["confirmation_phrase"]
    )
    responder = main.execute_operations_tool(
        db,
        "propose_runtime_change",
        {"action": "disable_anonymous_autoresponder", "reason": "Pause the line-two greeting."},
        "Pause it",
    )
    responder_result = main.execute_operations_tool(
        db, "execute_runtime_change", {"action_id": responder["action_id"]}, responder["confirmation_phrase"]
    )

    assert avatar_result["current_settings"]["show_message_avatars"] is False
    assert responder_result["current_settings"]["first_contact_autoresponders"]["secondary"] is False
    assert responders["primary"]["enabled"] is True
    db.close()


def test_operations_coding_tools_are_wired_and_start_one_audited_task(monkeypatch):
    class FakeMCPClient:
        configured = True

    launched = []
    monkeypatch.setattr(main, "operations_mcp_client", FakeMCPClient())
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "workspace")
    monkeypatch.setattr(main, "_launch_operations_worker", lambda *args: launched.append(args))
    db = make_db()

    started = main.execute_operations_tool(
        db,
        "start_coding_task",
        {
            "title": "Fix chronological message display",
            "instructions": "Inspect the message list and ensure stable chronological ordering for equal timestamps.",
            "acceptance_test": "Focused message ordering tests and the frontend build pass.",
        },
        "Fix it",
    )
    duplicate = main.execute_operations_tool(
        db,
        "start_coding_task",
        {
            "title": "Try the same work again",
            "instructions": "Start another task while the first implementation is still active and running.",
            "acceptance_test": "It should not start.",
        },
        "Do it again",
    )
    inspected = main.execute_operations_tool(
        db, "inspect_coding_task", {"task_id": started["task_id"]}, "How is it going?"
    )

    assert started["status"] == "started"
    assert duplicate["status"] == "already_running"
    assert inspected["task"]["state"] == "starting"
    assert len(launched) == 1
    assert db.query(main.OperationsAction).filter(main.OperationsAction.action_type == "coding_task").count() == 1
    assert {item["name"] for item in main.OPERATIONS_AI_TOOLS} >= {
        "inspect_coding_bridge",
        "read_code_file",
        "start_coding_task",
        "inspect_coding_task",
        "inspect_code_changes",
        "inspect_deployments",
        "propose_code_deployment",
        "execute_code_deployment",
    }
    db.close()


def test_code_deployment_requires_separate_exact_confirmation(monkeypatch):
    class FakeMCPClient:
        configured = True

    launched = []
    monkeypatch.setattr(main, "operations_mcp_client", FakeMCPClient())
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "workspace")
    monkeypatch.setenv("OPS_AGENT_ALLOW_DEPLOY", "true")
    monkeypatch.setattr(main, "_launch_operations_worker", lambda *args: launched.append(args))
    db = make_db()
    task = main.OperationsAction(
        action_type="coding_task",
        payload='{"title":"Fix","branch":"ops/test","commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","worktree_path":"F:\\\\worktree"}',
        reason="Owner-authorised coding task",
        status="completed",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    proposed = main.execute_operations_tool(
        db,
        "propose_code_deployment",
        {"task_id": task.id, "reason": "The implementation and focused checks passed."},
        "Deploy it",
    )
    second_task = main.OperationsAction(
        action_type="coding_task",
        payload='{"title":"Second fix","branch":"ops/second","commit_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","worktree_path":"F:\\\\second"}',
        reason="Another owner-authorised coding task",
        status="completed",
    )
    db.add(second_task)
    db.commit()
    db.refresh(second_task)
    busy = main.execute_operations_tool(
        db,
        "propose_code_deployment",
        {"task_id": second_task.id, "reason": "The second task also passed its checks."},
        "Deploy the second one",
    )
    rejected = main.execute_operations_tool(
        db, "execute_code_deployment", {"action_id": proposed["action_id"]}, "yes deploy it"
    )
    started = main.execute_operations_tool(
        db,
        "execute_code_deployment",
        {"action_id": proposed["action_id"]},
        proposed["confirmation_phrase"],
    )

    assert proposed["status"] == "pending_confirmation"
    assert busy["status"] == "deployment_busy"
    assert busy["action_id"] == proposed["action_id"]
    assert rejected["status"] == "rejected"
    assert started["status"] == "deployment_started"
    assert len(launched) == 1
    db.close()


def test_code_file_reader_blocks_secrets_before_calling_bridge(monkeypatch):
    class FakeMCPClient:
        configured = True

        def call_tool(self, *_args, **_kwargs):
            raise AssertionError("blocked paths must not reach the bridge")

    monkeypatch.setattr(main, "operations_mcp_client", FakeMCPClient())
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "workspace")

    result = main.execute_operations_tool(
        make_db(),
        "read_code_file",
        {"path": ".vscode/settings.json", "start_line": 1, "end_line": 20},
        "Read it",
    )

    assert result["status"] == "rejected"
    assert "not available" in result["reason"]


def test_operations_tool_does_not_expose_sms_credentials(monkeypatch):
    monkeypatch.setattr(main.mobilemessage_service, "load_accounts_config", lambda: {
        "primary": {
            "username": "private-user",
            "password": "private-password",
            "sender": "61400000010",
            "enabled": True,
        },
    })
    monkeypatch.setattr(main, "load_first_contact_autoresponders", lambda: {
        "primary": {"enabled": True, "cooldownDays": 1, "delaySeconds": 5, "message": "Hello"},
    })
    result = main._operations_sms_accounts()
    serialized = str(result)

    assert result["accounts"]["primary"]["credentials_configured"] is True
    assert "private-user" not in serialized
    assert "private-password" not in serialized


def test_operations_chat_executes_read_tool_and_returns_evidence(monkeypatch):
    first = type("Response", (), {
        "output": [FakeFunctionCall("inspect_system_status", "{}", "status-call")],
        "output_text": "",
    })()
    second = type("Response", (), {
        "output": [],
        "output_text": "I inspected the current status.",
    })()
    responses = SequenceResponses([first, second])
    monkeypatch.setattr(main, "openai_client", type("Client", (), {"responses": responses})())
    monkeypatch.setattr(main, "load_booking_services", lambda: [])
    monkeypatch.setattr(main, "load_working_hours", lambda: [])
    db = make_db()

    result = main.send_operations_chat_message(
        OperationsChatInput(message="Inspect the system status."),
        db,
    )

    assert result["assistantMessage"]["content"] == "I inspected the current status."
    assert len(responses.calls) == 2
    second_input = responses.calls[1]["input"]
    assert any(item.get("type") == "function_call_output" for item in second_input)
    assert '"status": "ok"' in str(second_input)
    db.close()


def test_operations_message_diagnostics_find_reply_and_failure_patterns():
    db = make_db()
    now = datetime.utcnow()
    thread = Thread(
        id="diagnostic-thread",
        customer_phone="+61412345678",
        sms_account_key="secondary",
        state="needs-review",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
        updated_at=now,
    )
    db.add(thread)
    db.add_all([
        Message(id="m1", thread_id=thread.id, role="customer", text="Private text", at=now - timedelta(minutes=5)),
        Message(id="m2", thread_id=thread.id, role="agent", text="First reply", at=now - timedelta(minutes=4)),
        Message(id="m3", thread_id=thread.id, role="agent", text="Second reply", at=now - timedelta(minutes=3)),
        ThreadEvent(id="e1", thread_id=thread.id, type="ai-reply-failed", at=now - timedelta(minutes=2)),
    ])
    db.commit()

    result = main._operations_message_handling_diagnostics(db, 24, 100)

    assert result["account_thread_counts"] == {"secondary": 1}
    assert result["sequencing"]["consecutive_agent_reply_pairs"] == 1
    assert result["reply_latency_seconds"]["samples"] == 1
    assert result["event_counts"]["ai-reply-failed"] == 1
    assert "Private text" not in str(result)
    assert "+61412345678" not in str(result)
    db.close()


def test_operations_memory_persists_and_rejects_private_data():
    db = make_db()
    remembered = main.execute_operations_tool(
        db,
        "remember_operational_learning",
        {
            "category": "behavior",
            "title": "Check availability before proposing times",
            "content": "Booking suggestions must be supported by a fresh availability check.",
            "evidence": "Confirmed during a message-handling diagnostic.",
        },
        "Remember this",
    )
    assert remembered["status"] == "remembered"
    assert db.query(OperationsMemory).count() == 1

    recalled = main.execute_operations_tool(
        db,
        "recall_operational_memory",
        {"query": "availability", "limit": 5},
        "Recall it",
    )
    assert recalled["memories"][0]["title"] == "Check availability before proposing times"

    rejected = main.execute_operations_tool(
        db,
        "remember_operational_learning",
        {
            "category": "incident",
            "title": "Customer report",
            "content": "A customer at +61432172148 reported a problem.",
            "evidence": "Conversation transcript",
        },
        "Remember it",
    )
    assert rejected["status"] == "rejected"
    assert db.query(OperationsMemory).count() == 1
    db.close()


def test_operations_web_research_rejects_private_query_before_provider_call(monkeypatch):
    client = FakeOpenAIClient("Should not be called")
    monkeypatch.setattr(main, "openai_client", client)

    result = main.execute_operations_tool(
        make_db(),
        "research_internet",
        {"query": "Look up this customer +61432172148", "reason": "Investigate delivery"},
        "Research it",
    )

    assert result["status"] == "rejected"
    assert client.responses.calls == []


def test_operations_web_research_uses_bounded_builtin_search(monkeypatch):
    client = FakeOpenAIClient("Use a queue with per-thread serialization.")
    monkeypatch.setattr(main, "openai_client", client)

    result = main.execute_operations_tool(
        make_db(),
        "research_internet",
        {"query": "reliable SMS webhook queue design", "reason": "Compare current architecture patterns"},
        "Research it",
    )

    assert result["status"] == "ok"
    assert client.responses.calls[0]["tools"] == [{"type": "web_search", "search_context_size": "medium"}]
    assert client.responses.calls[0]["store"] is False


def test_duplicate_improvement_proposal_reuses_existing_record():
    db = make_db()
    arguments = {
        "title": "Enforce line identity isolation",
        "description": "Keep every prompt and sender tied to its receiving SMS account.",
    }
    first = main.execute_operations_tool(db, "create_improvement_proposal", arguments, "Do it")
    second = main.execute_operations_tool(db, "create_improvement_proposal", arguments, "Proceed")

    assert first["status"] == "proposed"
    assert second["status"] == "already_proposed"
    assert second["proposal_id"] == first["proposal_id"]
    assert db.query(main.OperationsAction).filter(
        main.OperationsAction.action_type == "improvement_proposal"
    ).count() == 1
    db.close()


def test_realtime_session_uses_server_key_and_current_voice_model(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHTTPResponse("v=0\r\no=answer")

    monkeypatch.setenv("OPENAI_API_KEY", "protected-test-key")
    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)

    answer = main.create_operations_realtime_session("v=0\r\no=offer", '{"status":"ok"}')

    assert answer == "v=0\r\no=answer"
    request = captured["request"]
    assert request.full_url == "https://api.openai.com/v1/realtime/calls"
    assert request.headers["Authorization"] == "Bearer protected-test-key"
    assert b'gpt-realtime-2.1' in request.data
    assert b'"voice": "marin"' in request.data
    assert b'find_message_threads' in request.data
    assert b'inspect_message_thread' in request.data
    assert b'read-only message diagnostic tools' in request.data
    assert b'protected-test-key' not in request.data
    assert captured["timeout"] == 20


def test_realtime_session_fails_closed_without_server_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(main.HTTPException) as exc_info:
        main.create_operations_realtime_session("v=0\r\no=offer", "{}")

    assert exc_info.value.status_code == 503


def test_voice_can_read_full_account_bound_thread_and_reply_events():
    db = make_db()
    now = datetime.utcnow()
    thread = Thread(
        id="voice-thread",
        customer_phone="+61432172148",
        sms_account_key="primary",
        state="needs-review",
        priority="medium",
        sla_due_at=now + timedelta(hours=1),
        unread_count=1,
    )
    db.add(thread)
    db.add_all([
        Message(id="voice-m1", thread_id=thread.id, role="customer", text="Can I come tomorrow?", at=now),
        Message(id="voice-m2", thread_id=thread.id, role="customer", text="Around three", at=now + timedelta(seconds=1)),
        ThreadEvent(
            id="voice-e1",
            thread_id=thread.id,
            type="ai-reply-missed",
            at=now + timedelta(seconds=2),
            meta='{"reason":"global-ai-off","message_id":"voice-m2"}',
        ),
    ])
    db.commit()

    found = main.execute_operations_voice_tool(
        db, "find_message_threads", {"phone": "0432172148", "account_key": "primary", "limit": 5}
    )
    inspected = main.execute_operations_voice_tool(
        db, "inspect_message_thread", {"thread_id": "voice-thread"}
    )

    assert found["threads"][0]["thread_id"] == "voice-thread"
    assert [item["text"] for item in inspected["messages"]] == ["Can I come tomorrow?", "Around three"]
    assert inspected["events"][0]["meta"]["reason"] == "global-ai-off"
    assert inspected["thread"]["line"] == "Tori"
    db.close()


def test_voice_tool_endpoint_rejects_every_mutating_tool():
    db = make_db()
    result = main.execute_operations_voice_tool(
        db,
        "execute_runtime_change",
        {"action_id": "anything"},
    )
    assert result["status"] == "rejected"
    assert "read-only" in result["reason"]
    db.close()

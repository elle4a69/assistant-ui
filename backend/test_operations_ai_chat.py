from datetime import datetime, timedelta
from urllib import request as url_request
import uuid

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
    assert "When a workflow or deployment failed, inspect the failed run before asking the owner for anything" in call["instructions"]
    assert "Never leave the owner with a vague queued, waiting, or unavailable response" in call["instructions"]
    assert "Never answer a status request with a bare claim" in call["instructions"]
    assert call["tools"] == main.OPERATIONS_AI_TOOLS
    assert call["max_output_tokens"] == 1200
    assert "include" not in call
    owner_style = db.query(OperationsMemory).filter(
        OperationsMemory.title == main.OPERATIONS_OWNER_WORKING_STYLE_TITLE
    ).one()
    assert "complete and verify the work" in owner_style.content
    db.close()


def test_operations_chat_instructions_start_a_task_from_an_owner_described_fault(monkeypatch):
    monkeypatch.setattr(main, "operations_code_access_available", lambda: True)

    instructions = main.operations_ai_instructions('{"coding_runner_configured": true}')

    assert "Treat an owner-described fault, failed deployment, regression, or requested change as the task" in instructions
    assert "do not require the owner to supply a task ID, pull request, commit, branch, or implementation plan" in instructions
    assert "create the one deduplicated repair task yourself" in instructions


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
    class FakeGitHubClient:
        configured = True
        repository = "elle4a69/assistant-ui"

    github = FakeGitHubClient()
    monkeypatch.setattr(main, "operations_github_client", github)
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "github")
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
    assert inspected["task"]["state"] == "queued"
    assert db.query(main.OperationsAction).filter(main.OperationsAction.action_type == "coding_task").count() == 1
    saved = db.query(main.OperationsAction).filter(main.OperationsAction.action_type == "coding_task").one()
    assert main._operations_action_payload(saved)["stage"] == "awaiting_runner"
    assert {item["name"] for item in main.OPERATIONS_AI_TOOLS} >= {
        "inspect_coding_runner",
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
    class FakeGitHubClient:
        configured = True
        repository = "elle4a69/assistant-ui"

        def get_ref(self, ref):
            assert ref == "heads/main"
            return {"object": {"sha": "a" * 40}}

        def get_branch(self, branch):
            assert branch == "ops/task-one"
            return {"commit": {"sha": "b" * 40}}

        def get_git_commit(self, sha):
            assert sha == "b" * 40
            return {"sha": sha, "parents": [{"sha": "a" * 40}]}

        def compare(self, base, head):
            assert base == "a" * 40
            assert head == "ops/task-one"
            return {
                "status": "ahead",
                "ahead_by": 1,
                "files": [{"filename": "backend/main.py", "status": "modified"}],
            }

    github = FakeGitHubClient()
    monkeypatch.setattr(main, "operations_github_client", github)
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "github")
    monkeypatch.setenv("OPS_AGENT_ALLOW_DEPLOY", "true")
    db = make_db()
    task = main.OperationsAction(
        action_type="coding_task",
        payload='{"title":"Fix","branch":"ops/task-one","commit_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","base_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
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
        payload='{"title":"Second fix","branch":"ops/task-two","commit_sha":"cccccccccccccccccccccccccccccccccccccccc","base_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
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
    assert started["status"] == "deployment_queued"
    deployment = db.query(main.OperationsAction).filter(main.OperationsAction.id == proposed["action_id"]).one()
    assert deployment.status == "queued"
    db.close()


def test_completed_github_run_becomes_reviewable_task(monkeypatch):
    class FakeGitHubClient:
        configured = True
        repository = "elle4a69/assistant-ui"

        def get_workflow_run(self, run_id):
            assert run_id == 99
            return {
                "id": 99,
                "display_title": "Operations cloud queue",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.example/runs/99",
            }

        def get_branch(self, branch):
            assert branch == f"ops/task-{task.id}"
            return {"commit": {"sha": "d" * 40}}

        def compare(self, base, head):
            assert (base, head) == ("main", f"ops/task-{task.id}")
            return {
                "status": "ahead",
                "ahead_by": 1,
                "base_commit": {"sha": "a" * 40},
                "commits": [{"sha": "d" * 40}],
                "files": [{
                    "filename": "frontend/src/App.tsx",
                    "status": "modified",
                    "additions": 2,
                    "deletions": 1,
                    "changes": 3,
                }],
            }

    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "github")
    db = make_db()
    task = main.OperationsAction(
        action_type="coding_task",
        payload='{"title":"Cloud task","stage":"queued"}',
        reason="Owner-authorised coding task",
        status="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task.payload = '{"title":"Cloud task","stage":"queued","worker_run_id":"99","branch":"ops/task-' + task.id + '"}'
    db.commit()
    monkeypatch.setattr(main, "operations_github_client", FakeGitHubClient())

    inspected = main.execute_operations_tool(
        db, "inspect_coding_task", {"task_id": task.id}, "Check it"
    )
    changes = main.execute_operations_tool(
        db, "inspect_code_changes", {"task_id": task.id}, "Show changes"
    )

    assert inspected["task"]["state"] == "completed"
    assert inspected["task"]["commit_sha"] == "d" * 40
    assert changes["status"] == "ok"
    assert changes["files"] == [{
        "path": "frontend/src/App.tsx",
        "status": "modified",
        "additions": 2,
        "deletions": 1,
        "changes": 3,
    }]
    db.close()


@pytest.mark.parametrize("event_name", ["push", "schedule", "workflow_dispatch"])
def test_worker_claim_requires_exact_github_run(monkeypatch, event_name):
    task_sha = "a" * 40

    class FakeOIDCVerifier:
        def verify(self, token, *, audience):
            assert token == "signed-oidc-token"
            assert audience == "assistant-ui-hub-operations"
            return {
                "repository": "elle4a69/assistant-ui",
                "event_name": event_name,
                "ref": "refs/heads/main",
                "runner_environment": "github-hosted",
                "workflow": "Operations Cloud Coding",
                "workflow_ref": "elle4a69/assistant-ui/.github/workflows/operations-code.yml@refs/heads/main",
                "sha": task_sha,
                "workflow_sha": task_sha,
                "run_id": "321",
                "run_attempt": "1",
                "jti": "one-time-worker-identity",
            }

    class FakeGitHubClient:
        configured = True
        repository = "elle4a69/assistant-ui"

        def get_ref(self, ref):
            assert ref == "heads/main"
            return {"object": {"sha": task_sha}}

        def get_workflow_run(self, run_id):
            assert run_id == 321
            return {
                "id": 321,
                "display_title": "Operations cloud queue",
                "path": ".github/workflows/operations-code.yml",
                "head_sha": task_sha,
                "status": "in_progress",
                "event": event_name,
            }

    monkeypatch.setattr(main, "operations_github_oidc_verifier", FakeOIDCVerifier())
    monkeypatch.setattr(main, "operations_github_client", FakeGitHubClient())
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "github")
    monkeypatch.setenv("OPENAI_API_KEY", "worker-key-for-test")
    db = make_db()
    task = main.OperationsAction(
        action_type="coding_task",
        payload='{"title":"Cloud task","instructions":"Inspect the repository and make no unnecessary changes.","acceptance_test":"All focused tests pass."}',
        reason="Owner-authorised coding task",
        status="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_payload = main._operations_action_payload(task)
    task_payload["branch"] = "ops/task-" + task.id
    task.payload = main.json.dumps(task_payload)
    db.commit()

    result = main._operations_claim_worker_task(db, "signed-oidc-token")
    db.refresh(task)
    audit = main._operations_action_payload(task)

    assert result["kind"] == "coding"
    assert result["task_id"] == task.id
    assert result["credential"] == "worker-key-for-test"
    assert audit["worker_run_id"] == "321"
    assert audit["worker_claim_count"] == 1
    assert "worker-key-for-test" not in task.payload
    db.close()


def test_worker_claim_does_not_consume_coding_task_without_worker_credential(monkeypatch):
    task_sha = "a" * 40

    class FakeOIDCVerifier:
        def verify(self, _token, *, audience):
            assert audience == "assistant-ui-hub-operations"
            return {
                "repository": "elle4a69/assistant-ui",
                "event_name": "schedule",
                "ref": "refs/heads/main",
                "runner_environment": "github-hosted",
                "workflow": "Operations Cloud Coding",
                "workflow_ref": "elle4a69/assistant-ui/.github/workflows/operations-code.yml@refs/heads/main",
                "sha": task_sha,
                "workflow_sha": task_sha,
                "run_id": "654",
                "run_attempt": "1",
                "jti": "missing-credential-test",
            }

    class FakeGitHubClient:
        configured = True
        repository = "elle4a69/assistant-ui"

        def get_ref(self, _ref):
            return {"object": {"sha": task_sha}}

        def get_workflow_run(self, _run_id):
            return {
                "id": 654,
                "event": "schedule",
                "display_title": "Operations cloud queue",
                "path": ".github/workflows/operations-code.yml",
                "head_sha": task_sha,
                "status": "in_progress",
            }

    monkeypatch.setattr(main, "operations_github_oidc_verifier", FakeOIDCVerifier())
    monkeypatch.setattr(main, "operations_github_client", FakeGitHubClient())
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "github")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db = make_db()
    task = main.OperationsAction(
        action_type="coding_task",
        payload=(
            '{"title":"Cloud task","instructions":"Inspect the repository and make no unnecessary changes.",'
            '"acceptance_test":"All focused tests pass.","branch":"ops/task-placeholder"}'
        ),
        reason="Owner-authorised coding task",
        status="queued",
    )
    db.add(task)
    db.commit()

    with pytest.raises(main.HTTPException) as exc_info:
        main._operations_claim_worker_task(db, "signed-oidc-token")
    db.refresh(task)

    assert exc_info.value.status_code == 503
    assert task.status == "queued"
    assert "worker_run_id" not in main._operations_action_payload(task)
    db.close()


def test_verified_worker_gets_versioned_empty_queue_response(monkeypatch):
    monkeypatch.setattr(
        main,
        "_operations_verified_queue_run",
        lambda _token: ({
            "run_id": "765",
            "run_attempt": "1",
            "jti": "empty-queue-worker",
            "event_name": "schedule",
        }, {}, "a" * 40),
    )
    db = make_db()

    result = main._operations_claim_worker_task(db, "signed-oidc-token")

    assert result == {
        "protocol_version": main.OPERATIONS_WORKER_PROTOCOL_VERSION,
        "kind": "none",
    }
    db.close()


@pytest.mark.parametrize(
    ("conclusion", "expected_state"),
    [("success", "pushed"), ("failure", "failed")],
)
def test_deployment_reconciliation_requires_successful_worker(monkeypatch, conclusion, expected_state):
    expected_commit = "b" * 40

    class FakeGitHubClient:
        def get_ref(self, _ref):
            return {"object": {"sha": expected_commit}}

        def get_workflow_run(self, run_id):
            assert run_id == 987
            return {"status": "completed", "conclusion": conclusion}

    monkeypatch.setattr(main, "operations_github_client", FakeGitHubClient())
    db = make_db()
    action = main.OperationsAction(
        action_type="code_deployment",
        payload='{"worker_run_id":"987","commit_sha":"' + expected_commit + '"}',
        reason="Owner-confirmed deployment",
        status="running",
    )
    db.add(action)
    db.commit()

    main._operations_reconcile_deployment_actions(db)
    db.refresh(action)

    assert action.status == expected_state
    assert main._operations_action_payload(action)["stage"] == expected_state
    db.close()


def test_code_file_reader_blocks_secrets_before_calling_github(monkeypatch):
    class FakeGitHubClient:
        configured = True
        repository = "elle4a69/assistant-ui"

        def read_file(self, *_args, **_kwargs):
            raise AssertionError("blocked paths must not reach GitHub")

    monkeypatch.setattr(main, "operations_github_client", FakeGitHubClient())
    monkeypatch.setattr(main, "AUTH_PASSWORD", "configured-admin-password")
    monkeypatch.setenv("OPS_AGENT_CODE_MODE", "github")

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

    answer = main.create_operations_realtime_session(
        "v=0\r\no=offer",
        '{"status":"ok"}',
        '[{"title":"Keep reports direct"}]',
        "Recent authenticated conversation:\nOwner: Check the earlier repair.\nAssistant: I will inspect it.",
    )

    assert answer == "v=0\r\no=answer"
    request = captured["request"]
    assert request.full_url == "https://api.openai.com/v1/realtime/calls"
    assert request.headers["Authorization"] == "Bearer protected-test-key"
    assert b'gpt-realtime-2.1' in request.data
    assert b'"voice": "marin"' in request.data
    assert b'"transcription": {"model": "gpt-4o-mini-transcribe", "language": "en"}' in request.data
    assert b'"type": "server_vad"' in request.data
    assert b'"interrupt_response": true' in request.data
    assert b'find_message_threads' in request.data
    assert b'inspect_message_thread' in request.data
    assert b'start_coding_task' in request.data
    assert b'"strict": true' not in request.data
    assert "execute_code_deployment" not in main.OPERATIONS_VOICE_TOOL_NAMES
    assert "execute_runtime_change" not in main.OPERATIONS_VOICE_TOOL_NAMES
    assert b'Check the earlier repair' in request.data
    assert b'voice exchange is saved' in request.data
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


def test_voice_tools_allow_review_branch_coding_but_reject_protected_execution(monkeypatch):
    db = make_db()
    monkeypatch.setattr(main, "_operations_start_coding_task", lambda *_args: {
        "status": "queued",
        "task_id": "voice-review-task",
    })

    queued = main.execute_operations_voice_tool(
        db,
        "start_coding_task",
        {
            "title": "Repair the simulator",
            "instructions": "Inspect and repair the internal simulator with mocked provider tests.",
            "acceptance_test": "Backend and frontend tests pass.",
        },
    )
    rejected = main.execute_operations_voice_tool(
        db,
        "execute_runtime_change",
        {"action_id": "anything"},
    )

    assert queued == {"status": "queued", "task_id": "voice-review-task"}
    assert rejected["status"] == "rejected"
    assert "typed confirmation" in rejected["reason"]
    assert "start_coding_task" in main.OPERATIONS_VOICE_TOOL_NAMES
    assert "inspect_deployments" in main.OPERATIONS_VOICE_TOOL_NAMES
    assert "execute_runtime_change" not in main.OPERATIONS_VOICE_TOOL_NAMES
    assert "execute_code_deployment" not in main.OPERATIONS_VOICE_TOOL_NAMES
    db.close()


def test_realtime_voice_turn_is_chronological_sanitised_and_idempotent():
    db = make_db()
    session_id = str(uuid.uuid4())
    payload = main.OperationsRealtimeTurnInput(
        sessionId=session_id,
        userItemId="item-owner-1",
        responseId="response-assistant-1",
        userTranscript="Please check it. FLY_API_TOKEN=do-not-store-this",
        assistantTranscript="I checked the current deployment and it is healthy.",
    )

    first = main.persist_operations_realtime_turn(payload, db)
    second = main.persist_operations_realtime_turn(payload, db)
    rows = db.query(OperationsChatMessage).order_by(
        OperationsChatMessage.created_at,
        OperationsChatMessage.id,
    ).all()

    assert first["persisted"] is True
    assert second["persisted"] is False
    assert len(rows) == 2
    assert [item.role for item in rows] == ["user", "assistant"]
    assert rows[0].created_at < rows[1].created_at
    assert "do-not-store-this" not in rows[0].content
    assert "[REDACTED]" in rows[0].content
    assert [item["id"] for item in first["messages"]] == [item["id"] for item in second["messages"]]
    db.close()

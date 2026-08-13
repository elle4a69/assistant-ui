from datetime import datetime, timedelta
from urllib import request as url_request

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from main import Base, OperationsChatInput, OperationsChatMessage, Thread


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
        "controlledActions": True,
        "requiresConfirmation": True,
    }
    assert [item.role for item in db.query(OperationsChatMessage).all()] == ["user", "assistant"]
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-terra"
    assert call["store"] is False
    assert '"needs_review_count": 1' in call["instructions"]
    assert "cannot edit source code" in call["instructions"]
    assert call["tools"] == main.OPERATIONS_TOOL_SCHEMAS
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
    assert b'protected-test-key' not in request.data
    assert captured["timeout"] == 20


def test_realtime_session_fails_closed_without_server_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(main.HTTPException) as exc_info:
        main.create_operations_realtime_session("v=0\r\no=offer", "{}")

    assert exc_info.value.status_code == 503

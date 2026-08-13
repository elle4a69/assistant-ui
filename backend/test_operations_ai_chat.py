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
        "readOnly": True,
        "liveSnapshot": True,
        "codeAccess": False,
        "logAccess": False,
    }
    assert [item.role for item in db.query(OperationsChatMessage).all()] == ["user", "assistant"]
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-terra"
    assert call["store"] is False
    assert '"needs_review_count": 1' in call["instructions"]
    assert "cannot change settings" in call["instructions"]
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

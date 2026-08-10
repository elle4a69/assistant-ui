from unittest.mock import Mock

import mobilemessage_service
import main


def configured(monkeypatch):
    monkeypatch.setattr(
        mobilemessage_service,
        "load_config",
        lambda: {"username": "user", "password": "pass", "sender": "", "enabled": True},
    )
    monkeypatch.setattr(mobilemessage_service, "_resolved_sender", None)


def test_send_uses_default_registered_sender_and_idempotency_key(monkeypatch):
    configured(monkeypatch)
    sender_response = Mock(ok=True)
    sender_response.json.return_value = {
        "results": [{"sender": "61400000000", "is_default": True}]
    }
    send_response = Mock(ok=True, status_code=200)
    send_response.json.return_value = {
        "status": "complete",
        "results": [{"status": "success", "message_id": "one"}],
    }
    monkeypatch.setattr(mobilemessage_service.requests, "get", Mock(return_value=sender_response))
    post = Mock(return_value=send_response)
    monkeypatch.setattr(mobilemessage_service.requests, "post", post)

    result = mobilemessage_service.send_sms("+61411111111", "Hello", idempotency_key="reply-1")

    assert result["status"] == "success"
    assert post.call_args.kwargs["json"]["messages"][0]["sender"] == "61400000000"
    assert post.call_args.kwargs["headers"] == {"Idempotency-Key": "reply-1"}


def test_http_200_with_rejected_message_is_an_error(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setattr(mobilemessage_service, "_resolved_sender", "61400000000")
    send_response = Mock(ok=True, status_code=200)
    send_response.json.return_value = {
        "status": "complete",
        "results": [{"status": "error", "error": "sender is required"}],
    }
    monkeypatch.setattr(mobilemessage_service.requests, "post", Mock(return_value=send_response))

    result = mobilemessage_service.send_sms("0411111111", "Hello")

    assert result["status"] == "error"
    assert mobilemessage_service.delivery_error(result)


def test_gateway_settings_do_not_expose_saved_password(monkeypatch):
    monkeypatch.setattr(
        main.mobilemessage_service,
        "load_config",
        lambda: {
            "username": "user",
            "password": "secret",
            "sender": "61400000000",
            "enabled": True,
        },
    )

    result = main.get_mobilemessage_settings()

    assert result["password"] == ""
    assert result["hasPassword"] is True

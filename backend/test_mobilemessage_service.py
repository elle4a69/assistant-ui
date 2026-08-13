from unittest.mock import Mock

import pytest
from fastapi import HTTPException

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


def test_invalid_australian_mobile_is_blocked_before_gateway_call(monkeypatch):
    configured(monkeypatch)
    get = Mock()
    post = Mock()
    monkeypatch.setattr(mobilemessage_service.requests, "get", get)
    monkeypatch.setattr(mobilemessage_service.requests, "post", post)

    result = mobilemessage_service.send_sms("+614123456789", "Hello")

    assert result == {
        "status": "error",
        "reason": (
            "Invalid destination phone number. Use an Australian mobile in "
            "04xx xxx xxx or +614xx xxx xxx format."
        ),
    }
    get.assert_not_called()
    post.assert_not_called()


def test_local_australian_mobile_is_normalized_for_gateway(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setattr(mobilemessage_service, "_resolved_sender", "61400000000")
    send_response = Mock(ok=True, status_code=200)
    send_response.json.return_value = {
        "status": "complete",
        "results": [{"status": "success", "message_id": "one"}],
    }
    post = Mock(return_value=send_response)
    monkeypatch.setattr(mobilemessage_service.requests, "post", post)

    result = mobilemessage_service.send_sms("0432 172 314", "Hello")

    assert result["status"] == "success"
    assert post.call_args.kwargs["json"]["messages"][0]["to"] == "61432172314"


def test_booking_rejects_invalid_mobile_before_calendar_or_sms():
    db = Mock()
    payload = main.ManualBookingInput(
        serviceId="service-one",
        name="Test Customer",
        phone="+614123456789",
        startTime="2026-08-12T10:00:00Z",
    )

    with pytest.raises(HTTPException) as exc_info:
        main.create_manual_booking(payload, db)

    assert exc_info.value.status_code == 422
    assert "valid Australian mobile" in exc_info.value.detail
    db.rollback.assert_not_called()


def test_secondary_account_uses_its_own_credentials_and_sender(monkeypatch):
    configs = {
        "primary": {"username": "one", "password": "p1", "sender": "61400000010", "enabled": True},
        "secondary": {"username": "two", "password": "p2", "sender": "61420136756", "enabled": True},
    }
    monkeypatch.setattr(mobilemessage_service, "load_config", lambda key="primary": configs[key])
    send_response = Mock(ok=True, status_code=200)
    send_response.json.return_value = {"results": [{"status": "success", "message_id": "two"}]}
    post = Mock(return_value=send_response)
    monkeypatch.setattr(mobilemessage_service.requests, "post", post)

    result = mobilemessage_service.send_sms(
        "0411111111",
        "Hello from line two",
        account_key="secondary",
    )

    assert result["status"] == "success"
    assert post.call_args.kwargs["auth"].username == "two"
    assert post.call_args.kwargs["json"]["messages"][0]["sender"] == "61420136756"


def test_account_rejects_sender_from_the_other_line(monkeypatch):
    configs = {
        "primary": {"username": "one", "password": "p1", "sender": "61400000010", "enabled": True},
        "secondary": {"username": "two", "password": "p2", "sender": "61420136756", "enabled": True},
    }
    monkeypatch.setattr(mobilemessage_service, "load_config", lambda key="primary": configs[key])
    post = Mock()
    monkeypatch.setattr(mobilemessage_service.requests, "post", post)

    result = mobilemessage_service.send_sms(
        "0411111111",
        "Wrong identity",
        custom_sender="61400000010",
        account_key="secondary",
    )

    assert result["status"] == "error"
    assert "does not belong" in result["reason"]
    post.assert_not_called()


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

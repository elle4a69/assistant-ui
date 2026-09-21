"""Regression coverage for the temporary Line 1-only operator account."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

import main


def _request_with_session(token: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/threads",
        "raw_path": b"/api/threads",
        "query_string": b"",
        "headers": [(b"cookie", f"{main.AUTH_COOKIE_NAME}={token}".encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    })


def _database():
    engine = create_engine("sqlite:///:memory:")
    main.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    db.add_all([
        main.Thread(
            id="line-one-thread", customer_phone="+611", sms_account_key="primary",
            state="auto-reply", priority="medium", sla_due_at=now + timedelta(hours=1),
        ),
        main.Thread(
            id="line-two-thread", customer_phone="+622", sms_account_key="secondary",
            state="auto-reply", priority="medium", sla_due_at=now + timedelta(hours=1),
        ),
    ])
    db.commit()
    return db


def test_existing_operator_is_limited_to_line_one_while_2lines_sees_both(monkeypatch):
    monkeypatch.setattr(main, "AUTH_USERNAME", "existing-operator")
    monkeypatch.setattr(main, "AUTH_PASSWORD", "operator-password")
    monkeypatch.setattr(main, "FULL_ACCESS_USERNAME", "2lines")
    monkeypatch.setattr(main, "FULL_ACCESS_PASSWORD", "full-access-password")
    db = _database()
    expiry = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())

    operator_request = _request_with_session(main._admin_session_token(expiry))
    operator_threads = main.get_threads(None, None, None, None, db, operator_request)
    assert [thread["smsAccountKey"] for thread in operator_threads] == ["primary"]
    with pytest.raises(HTTPException, match="Thread not found"):
        main.get_thread_detail("line-two-thread", db, operator_request)

    full_access_request = _request_with_session(main._admin_session_token(expiry, "2lines"))
    full_access_threads = main.get_threads(None, None, None, None, db, full_access_request)
    assert {thread["smsAccountKey"] for thread in full_access_threads} == {"primary", "secondary"}
    assert main.get_thread_detail("line-two-thread", db, full_access_request)["smsAccountKey"] == "secondary"


def test_live_thread_and_arrival_routes_receive_the_authenticated_request():
    request_parameter_by_path = {
        route.path: route.dependant.request_param_name
        for route in main.app.routes
        if route.path in {
            "/api/threads",
            "/api/threads/{thread_id}",
            "/api/arrival/admin/sessions",
        }
    }
    assert request_parameter_by_path == {
        "/api/threads": "request",
        "/api/threads/{thread_id}": "request",
        "/api/arrival/admin/sessions": "request",
    }

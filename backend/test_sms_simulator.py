import base64

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main


def _basic_auth(username: str = "admin", password: str = "simulator-test-password") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_simulator_separates_lines_and_never_dispatches_provider_sms(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    main.Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)

    def override_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = override_db
    monkeypatch.setattr(main, "AUTH_PASSWORD", "simulator-test-password")
    monkeypatch.setattr(main, "AUTO_REPLY_GLOBAL_ENABLED", True)
    monkeypatch.setattr(main, "account_allows_conversational_ai", lambda _key: True)
    monkeypatch.setattr(
        main,
        "load_first_contact_autoresponder",
        lambda _key="primary": {
            "enabled": False,
            "cooldownDays": 1,
            "delaySeconds": 0,
            "message": "",
        },
    )

    reply_calls = []

    def fake_reply_logic(db, thread_id, body, provider_message_id, received_at, **kwargs):
        thread = db.query(main.Thread).filter(main.Thread.id == thread_id).one()
        reply_calls.append((thread.sms_account_key, body, kwargs["dispatch_sms"], kwargs["is_simulation"]))
        return False, False

    monkeypatch.setattr(main, "run_sms_reply_logic", fake_reply_logic)
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider send attempted")),
    )

    try:
        client = TestClient(main.app)
        requests = [
            ("primary", "Hello Tori"),
            ("secondary", "Hello Anonymous"),
        ]
        responses = [
            client.post(
                "/api/admin/sms-simulator",
                headers=_basic_auth(),
                json={
                    "customer_phone": "0412 345 678",
                    "body": body,
                    "sms_account_key": account_key,
                },
            )
            for account_key, body in requests
        ]

        assert [response.status_code for response in responses] == [200, 200]
        assert responses[0].json()["thread_id"] != responses[1].json()["thread_id"]
        assert [response.json()["provider_sends"] for response in responses] == [0, 0]
        assert reply_calls == [
            ("primary", "Hello Tori", False, True),
            ("secondary", "Hello Anonymous", False, True),
        ]

        with testing_session() as db:
            threads = db.query(main.Thread).order_by(main.Thread.sms_account_key).all()
            assert [(thread.sms_account_key, thread.customer_phone) for thread in threads] == [
                ("primary", "+61412345678"),
                ("secondary", "+61412345678"),
            ]
    finally:
        main.app.dependency_overrides.clear()


def test_admin_simulator_requires_auth_and_reports_invalid_input(monkeypatch):
    monkeypatch.setattr(main, "AUTH_PASSWORD", "simulator-test-password")
    client = TestClient(main.app)
    payload = {
        "customer_phone": "not-a-phone",
        "body": "Hello",
        "sms_account_key": "primary",
    }

    assert client.post("/api/admin/sms-simulator", json=payload).status_code == 401

    invalid_phone = client.post(
        "/api/admin/sms-simulator",
        headers=_basic_auth(),
        json=payload,
    )
    assert invalid_phone.status_code == 422
    assert "valid Australian mobile or E.164" in invalid_phone.json()["detail"]

    invalid_account = client.post(
        "/api/admin/sms-simulator",
        headers=_basic_auth(),
        json={**payload, "customer_phone": "0412 345 678", "sms_account_key": "unknown"},
    )
    assert invalid_account.status_code == 422
    assert invalid_account.json()["detail"][0]["loc"][-1] == "sms_account_key"

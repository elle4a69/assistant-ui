import base64

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main


def _basic_auth() -> dict[str, str]:
    token = base64.b64encode(b"admin:bulk-sms-test-password").decode()
    return {"Authorization": f"Basic {token}"}


def test_bulk_sms_validates_deduplicates_and_records_each_delivery(monkeypatch):
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
    monkeypatch.setattr(main, "AUTH_PASSWORD", "bulk-sms-test-password")
    sent = []
    monkeypatch.setattr(
        main.mobilemessage_service,
        "send_sms",
        lambda phone, text, idempotency_key=None, account_key="primary": sent.append(
            (phone, text, idempotency_key, account_key)
        ) or {"status": "success"},
    )
    monkeypatch.setattr(main.mobilemessage_service, "delivery_error", lambda _result: None)

    payload = {
        "recipients": ["0412 345 678", "+61412345678", "0412 345 679"],
        "message": "A generic update for everyone.",
        "smsAccountKey": "secondary",
        "clientRequestId": "owner-confirmed-bulk-1",
    }
    try:
        client = TestClient(main.app)
        response = client.post("/api/settings/bulk-sms", headers=_basic_auth(), json=payload)

        assert response.status_code == 200
        assert response.json()["requested"] == 2
        assert response.json()["sent"] == 2
        assert response.json()["failed"] == 0
        assert [(phone, account_key) for phone, _, _, account_key in sent] == [
            ("+61412345678", "secondary"),
            ("+61412345679", "secondary"),
        ]

        retry = client.post("/api/settings/bulk-sms", headers=_basic_auth(), json=payload)
        assert retry.status_code == 200
        assert retry.json()["sent"] == 0
        assert retry.json()["duplicates"] == 2
        assert len(sent) == 2

        with testing_session() as db:
            assert db.query(main.Thread).count() == 2
            assert db.query(main.Message).filter(main.Message.role == "agent").count() == 2
            assert db.query(main.ThreadEvent).filter(main.ThreadEvent.type == "bulk-sms-sent").count() == 2

        invalid = client.post(
            "/api/settings/bulk-sms",
            headers=_basic_auth(),
            json={**payload, "recipients": ["not-a-mobile"], "clientRequestId": "owner-confirmed-bulk-2"},
        )
        assert invalid.status_code == 422
        assert len(sent) == 2
    finally:
        main.app.dependency_overrides.clear()

import base64
import csv
import io
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main


def _basic_auth(password: str = "export-test-password") -> dict[str, str]:
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _export_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    main.Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)

    def override_db():
        with testing_session() as db:
            yield db

    main.app.dependency_overrides[main.get_db] = override_db
    monkeypatch.setattr(main, "AUTH_PASSWORD", "export-test-password")
    return TestClient(main.app), testing_session


def test_message_csv_export_requires_auth_and_safely_exports_all_thread_fields(monkeypatch):
    client, testing_session = _export_client(monkeypatch)
    now = datetime(2026, 8, 30, 12, 30, 0)
    try:
        with testing_session() as db:
            db.add_all([
                main.Thread(
                    id="conversation-1",
                    customer_phone="+61412345678",
                    sms_account_key="primary",
                    sla_due_at=now + timedelta(hours=1),
                ),
                main.Thread(
                    id="=unsafe-conversation",
                    customer_phone="@unsafe-contact",
                    sms_account_key="secondary",
                    sla_due_at=now + timedelta(hours=1),
                ),
            ])
            db.flush()
            db.add_all([
                main.Message(
                    id="message-1",
                    thread_id="conversation-1",
                    role="customer",
                    text='Hello, "team"\nSecond line',
                    at=now,
                ),
                main.Message(
                    id="message-2",
                    thread_id="=unsafe-conversation",
                    role="agent",
                    text="  =HYPERLINK(\"https://example.invalid\")",
                    at=now + timedelta(seconds=1),
                ),
            ])
            db.commit()

        assert client.get("/api/settings/messages/export.csv").status_code == 401

        response = client.get(
            "/api/settings/messages/export.csv",
            headers=_basic_auth(),
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].startswith(
            'attachment; filename="messages-export-'
        )
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert rows == [
            {
                "account_identifier": "primary",
                "timestamp": "2026-08-30T12:30:00Z",
                "direction": "inbound",
                "message_body": 'Hello, "team"\nSecond line',
                "conversation_reference": "conversation-1",
                "contact_reference": "'+61412345678",
            },
            {
                "account_identifier": "secondary",
                "timestamp": "2026-08-30T12:30:01Z",
                "direction": "outbound",
                "message_body": "'  =HYPERLINK(\"https://example.invalid\")",
                "conversation_reference": "'=unsafe-conversation",
                "contact_reference": "'@unsafe-contact",
            },
        ]
    finally:
        main.app.dependency_overrides.clear()


def test_message_csv_export_empty_dataset_returns_header_only(monkeypatch):
    client, _testing_session = _export_client(monkeypatch)
    try:
        response = client.get(
            "/api/settings/messages/export.csv",
            headers=_basic_auth(),
        )
        assert response.status_code == 200
        assert response.text == (
            "account_identifier,timestamp,direction,message_body,"
            "conversation_reference,contact_reference\r\n"
        )
    finally:
        main.app.dependency_overrides.clear()

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


def test_conversation_csv_requires_auth_and_preserves_line_scope_and_escaping(monkeypatch):
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
    monkeypatch.setattr(main, "load_line_profiles", lambda: {
        "primary": {"displayName": "Tori, Main"},
        "secondary": {"displayName": "Anonymous"},
    })

    now = datetime(2026, 8, 30, 12, 34, 56)
    with testing_session() as db:
        db.add_all([
            main.Thread(
                id="primary-conversation",
                customer_phone="+61400000001",
                sms_account_key="primary",
                state="auto-reply",
                priority="medium",
                sla_due_at=now + timedelta(hours=1),
                created_at=now,
                updated_at=now,
            ),
            main.Thread(
                id="secondary-conversation",
                customer_phone="+61400000002",
                sms_account_key="secondary",
                state="auto-reply",
                priority="medium",
                sla_due_at=now + timedelta(hours=1),
                created_at=now,
                updated_at=now,
            ),
        ])
        db.add_all([
            main.Message(
                id="primary-message",
                thread_id="primary-conversation",
                role="customer",
                text='Hello, "Tori"\nCan you help? café',
                at=now,
            ),
            main.Message(
                id="secondary-message",
                thread_id="secondary-conversation",
                role="agent",
                text="This must not cross line scope",
                at=now + timedelta(seconds=1),
            ),
        ])
        db.commit()

    try:
        unauthenticated = TestClient(main.app).get(
            "/api/settings/conversations/export.csv?smsAccountKey=primary"
        )
        assert unauthenticated.status_code == 401

        response = TestClient(main.app).get(
            "/api/settings/conversations/export.csv?smsAccountKey=primary",
            headers=_basic_auth(),
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert response.headers["content-disposition"].startswith(
            'attachment; filename="conversation-messages-primary-'
        )
        assert response.content.startswith(b"\xef\xbb\xbf")

        decoded = response.content.decode("utf-8-sig")
        assert '"Tori, Main"' in decoded
        assert '"Hello, ""Tori""\nCan you help? café"' in decoded
        records = list(csv.DictReader(io.StringIO(decoded, newline="")))
        assert records == [{
            "sms_account_key": "primary",
            "line_display_name": "Tori, Main",
            "conversation_id": "primary-conversation",
            "message_id": "primary-message",
            "direction": "inbound",
            "message_role": "customer",
            "timestamp_utc": "2026-08-30T12:34:56Z",
            "message_content": 'Hello, "Tori"\nCan you help? café',
        }]
        assert "+61400000001" not in decoded
        assert "secondary-conversation" not in decoded
        assert "This must not cross line scope" not in decoded
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def test_conversation_csv_rejects_unknown_line_scope(monkeypatch):
    monkeypatch.setattr(main, "AUTH_PASSWORD", "export-test-password")
    response = TestClient(main.app).get(
        "/api/settings/conversations/export.csv?smsAccountKey=unknown",
        headers=_basic_auth(),
    )
    assert response.status_code == 422

import csv
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main


@pytest.fixture
def export_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    main.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = override_db
    monkeypatch.setattr(main, "AUTH_PASSWORD", "export-test-password")
    yield TestClient(main.app), factory
    main.app.dependency_overrides.clear()
    engine.dispose()


def sign_in(client):
    response = client.post("/api/auth/login", json={
        "username": main.AUTH_USERNAME,
        "password": "export-test-password",
    })
    assert response.status_code == 200


def add_thread(db, thread_id, account_key, phone, created_at):
    db.add(main.Thread(
        id=thread_id,
        customer_phone=phone,
        sms_account_key=account_key,
        state="auto-reply",
        priority="medium",
        sla_due_at=created_at + timedelta(hours=1),
        unread_count=0,
        created_at=created_at,
        updated_at=created_at,
    ))


def test_message_export_requires_auth_and_returns_header_for_empty_database(export_client):
    client, _factory = export_client

    assert client.get("/api/settings/messages/export").status_code == 401
    sign_in(client)

    response = client.get("/api/settings/messages/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="messages.csv"'
    assert list(csv.reader(io.StringIO(response.text))) == [list(main.MESSAGE_EXPORT_HEADERS)]


def test_message_export_is_stable_scoped_and_spreadsheet_safe(export_client):
    client, factory = export_client
    sign_in(client)
    earlier = datetime(2026, 8, 29, 10, 0, 0)
    later = datetime(2026, 8, 30, 11, 30, 0)
    with factory() as db:
        add_thread(db, "thread-secondary", "secondary", "+61400000002", earlier)
        add_thread(db, "thread-primary", "primary", "+61400000001", earlier)
        db.add_all([
            main.Message(
                id="message-later",
                thread_id="thread-primary",
                role="agent",
                text='A comma, a "quote", and\na newline',
                provider_message_id="provider-secret-must-not-export",
                at=later,
            ),
            main.Message(
                id="message-earlier",
                thread_id="thread-secondary",
                role="customer",
                text="  =HYPERLINK(\"https://example.invalid\")",
                provider_message_id="another-provider-secret",
                at=earlier,
            ),
        ])
        db.commit()

    response = client.get("/api/settings/messages/export")
    rows = list(csv.reader(io.StringIO(response.text)))

    assert rows == [
        list(main.MESSAGE_EXPORT_HEADERS),
        [
            "message-earlier",
            "thread-secondary",
            "secondary",
            "'+61400000002",
            "customer",
            "'  =HYPERLINK(\"https://example.invalid\")",
            "2026-08-29T10:00:00Z",
        ],
        [
            "message-later",
            "thread-primary",
            "primary",
            "'+61400000001",
            "agent",
            'A comma, a "quote", and\na newline',
            "2026-08-30T11:30:00Z",
        ],
    ]
    assert "provider-secret" not in response.text

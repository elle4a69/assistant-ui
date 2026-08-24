from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main


def test_existing_arrival_session_table_is_upgraded_additively(monkeypatch, tmp_path):
    database_path = tmp_path / "legacy-arrivals.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    now = datetime.utcnow()
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE calendar_events (
                id VARCHAR NOT NULL PRIMARY KEY,
                summary VARCHAR NOT NULL,
                customer_phone VARCHAR,
                start_time DATETIME NOT NULL,
                end_time DATETIME NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'scheduled',
                notes TEXT,
                created_at DATETIME NOT NULL
            )
        """)
        connection.exec_driver_sql("""
            CREATE TABLE arrival_sessions (
                id VARCHAR NOT NULL PRIMARY KEY,
                booking_id VARCHAR NOT NULL,
                invite_token_hash VARCHAR NOT NULL UNIQUE,
                client_token_hash VARCHAR UNIQUE,
                status VARCHAR NOT NULL,
                expires_at DATETIME NOT NULL,
                activated_at DATETIME,
                closed_at DATETIME,
                created_at DATETIME NOT NULL,
                last_activity_at DATETIME NOT NULL
            )
        """)
        connection.exec_driver_sql(
            """
            INSERT INTO arrival_sessions (
                id, booking_id, invite_token_hash, client_token_hash, status,
                expires_at, activated_at, closed_at, created_at, last_activity_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-session", "legacy-booking", "legacy-invite", None, "invited",
                now + timedelta(hours=2), None, None, now, now,
            ),
        )

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))
    main.init_db()

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(arrival_sessions)").fetchall()
        }
        assert {
            "thread_id", "sms_account_key", "arrival_event_id", "acknowledged_at",
            "last_alert_at", "next_alert_at", "alert_count",
        }.issubset(columns)
        booking_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(calendar_events)").fetchall()
        }
        assert {"sms_account_key", "thread_id"}.issubset(booking_columns)
        legacy = connection.exec_driver_sql(
            "SELECT id, status, alert_count, thread_id, sms_account_key FROM arrival_sessions WHERE id = ?",
            ("legacy-session",),
        ).one()
        assert tuple(legacy) == ("legacy-session", "invited", 0, None, None)

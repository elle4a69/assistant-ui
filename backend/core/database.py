"""Neutral database infrastructure leaf module.

Contains the SQLAlchemy declarative base, database engine configuration,
session factory, connection pooling, SQLite pragmas, and session dependencies.
This module MUST NOT import anything from `backend.main` or any router module.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session

try:
    from backend.core.config import DATABASE_URL
except ImportError:
    from core.config import DATABASE_URL

from sqlalchemy.pool import StaticPool

# SQLite connect arguments
sqlite_connect_args = (
    {"check_same_thread": False, "timeout": 30}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

# Engine singleton
if DATABASE_URL in ("sqlite:///:memory:", "sqlite://"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    engine = create_engine(DATABASE_URL, connect_args=sqlite_connect_args)

# Session factory singleton
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class ModelBase:
    """Declarative base class with automatic extend_existing for multi-context test safety."""
    __table_args__ = {"extend_existing": True}

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "__tablename__"):
            ta = getattr(cls, "__table_args__", None)
            if ta is None:
                cls.__table_args__ = {"extend_existing": True}
            elif isinstance(ta, dict):
                ta.setdefault("extend_existing", True)
            elif isinstance(ta, tuple):
                if ta and isinstance(ta[-1], dict):
                    ta[-1].setdefault("extend_existing", True)
                else:
                    cls.__table_args__ = ta + ({"extend_existing": True},)


# Declarative Base
Base = declarative_base(cls=ModelBase)


# SQLite connection pragmas listener
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode, foreign keys, and sensible busy timeouts on SQLite connections."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        # Pass silently if underlying connection driver does not support SQLite pragmas
        pass
    finally:
        cursor.close()


def _dyn_engine():
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "engine"):
            eng = getattr(mod, "engine")
            if eng is not None:
                return eng
    return engine


def _dyn_session_local():
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "SessionLocal"):
            factory = getattr(mod, "SessionLocal")
            if factory is not None:
                return factory
    return SessionLocal


def init_db():
    """Create database tables, run additive SQLite column migrations, and seed initial records if empty."""
    import uuid
    from datetime import datetime, timedelta

    eff_engine = _dyn_engine()
    Base.metadata.create_all(bind=eff_engine)

    # Auto-migration for SQLite columns on calendar_events
    try:
        with eff_engine.connect() as conn:
            try:
                result = conn.exec_driver_sql("PRAGMA table_info(calendar_events)").fetchall()
                col_names = [row[1] for row in result]
                if "status" not in col_names:
                    conn.exec_driver_sql("ALTER TABLE calendar_events ADD COLUMN status VARCHAR DEFAULT 'scheduled'")
                if "notes" not in col_names:
                    conn.exec_driver_sql("ALTER TABLE calendar_events ADD COLUMN notes TEXT")
                if "sms_account_key" not in col_names:
                    conn.exec_driver_sql("ALTER TABLE calendar_events ADD COLUMN sms_account_key VARCHAR")
                if "thread_id" not in col_names:
                    conn.exec_driver_sql("ALTER TABLE calendar_events ADD COLUMN thread_id VARCHAR")
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_calendar_events_sms_account_key "
                    "ON calendar_events (sms_account_key)"
                )
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_calendar_events_thread_id ON calendar_events (thread_id)"
                )
                conn.commit()
            except Exception:
                pass

            try:
                thread_columns = {
                    row[1] for row in conn.exec_driver_sql("PRAGMA table_info(threads)").fetchall()
                }
                if thread_columns:
                    unique_indexes = []
                    for index_row in conn.exec_driver_sql("PRAGMA index_list(threads)").fetchall():
                        if index_row[2]:
                            columns = [
                                row[2]
                                for row in conn.exec_driver_sql(
                                    f'PRAGMA index_info("{index_row[1]}")'
                                ).fetchall()
                            ]
                            unique_indexes.append(columns)
                    needs_sms_rebuild = (
                        "sms_account_key" not in thread_columns
                        or ["customer_phone"] in unique_indexes
                    )
                    if needs_sms_rebuild:
                        conn.commit()
                        raw = eff_engine.raw_connection()
                        cursor = raw.cursor()
                        try:
                            cursor.execute("PRAGMA foreign_keys=OFF")
                            cursor.execute("BEGIN IMMEDIATE")
                            cursor.execute("""
                                CREATE TABLE threads_dual_sms (
                                    id VARCHAR NOT NULL PRIMARY KEY,
                                    customer_phone VARCHAR NOT NULL,
                                    sms_account_key VARCHAR NOT NULL DEFAULT 'primary',
                                    state VARCHAR NOT NULL DEFAULT 'auto-reply',
                                    priority VARCHAR NOT NULL DEFAULT 'medium',
                                    assigned_agent_id VARCHAR,
                                    sla_due_at DATETIME NOT NULL,
                                    unread_count INTEGER NOT NULL DEFAULT 0,
                                    auto_reply_enabled BOOLEAN NOT NULL DEFAULT 1,
                                    pending_slots TEXT,
                                    pending_booking TEXT,
                                    created_at DATETIME NOT NULL,
                                    updated_at DATETIME NOT NULL,
                                    CONSTRAINT uq_threads_sms_account_phone
                                        UNIQUE (sms_account_key, customer_phone)
                                )
                            """)
                            pending_booking_expr = "pending_booking" if "pending_booking" in thread_columns else "NULL"
                            sms_account_expr = "COALESCE(sms_account_key, 'primary')" if "sms_account_key" in thread_columns else "'primary'"
                            cursor.execute(f"""
                                INSERT INTO threads_dual_sms (
                                    id, customer_phone, sms_account_key, state, priority,
                                    assigned_agent_id, sla_due_at, unread_count,
                                    auto_reply_enabled, pending_slots, pending_booking,
                                    created_at, updated_at
                                )
                                SELECT id, customer_phone, {sms_account_expr}, state, priority,
                                       assigned_agent_id, sla_due_at, unread_count,
                                       auto_reply_enabled, pending_slots, {pending_booking_expr},
                                       created_at, updated_at
                                FROM threads
                            """)
                            cursor.execute("DROP TABLE threads")
                            cursor.execute("ALTER TABLE threads_dual_sms RENAME TO threads")
                            cursor.execute("CREATE INDEX ix_threads_customer_phone ON threads (customer_phone)")
                            cursor.execute("CREATE INDEX ix_threads_sms_account_key ON threads (sms_account_key)")
                            raw.commit()
                        except Exception:
                            raw.rollback()
                            raise
                        finally:
                            cursor.execute("PRAGMA foreign_keys=ON")
                            cursor.close()
                            raw.close()
                    elif "pending_booking" not in thread_columns:
                        conn.exec_driver_sql("ALTER TABLE threads ADD COLUMN pending_booking TEXT")
                    conn.commit()
            except Exception:
                pass

            try:
                arrival_columns = {
                    row[1] for row in conn.exec_driver_sql("PRAGMA table_info(arrival_sessions)").fetchall()
                }
                if arrival_columns:
                    additive_columns = {
                        "thread_id": "VARCHAR",
                        "sms_account_key": "VARCHAR",
                        "arrival_event_id": "VARCHAR",
                        "acknowledged_at": "DATETIME",
                        "last_alert_at": "DATETIME",
                        "next_alert_at": "DATETIME",
                        "alert_count": "INTEGER NOT NULL DEFAULT 0",
                    }
                    for column_name, column_type in additive_columns.items():
                        if column_name not in arrival_columns:
                            conn.exec_driver_sql(
                                f'ALTER TABLE arrival_sessions ADD COLUMN "{column_name}" {column_type}'
                            )
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_arrival_sessions_thread_id ON arrival_sessions (thread_id)"
                    )
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_arrival_sessions_sms_account_key ON arrival_sessions (sms_account_key)"
                    )
                    conn.exec_driver_sql(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_arrival_sessions_arrival_event_id "
                        "ON arrival_sessions (arrival_event_id)"
                    )
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_arrival_sessions_acknowledged_at "
                        "ON arrival_sessions (acknowledged_at)"
                    )
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_arrival_sessions_next_alert_at "
                        "ON arrival_sessions (next_alert_at)"
                    )
                    conn.commit()
            except Exception:
                pass
    except Exception:
        pass

    # Seed sample initial bookings & threads if empty
    factory = _dyn_session_local()
    db = factory()
    try:
        try:
            from backend.models.domain import CalendarEvent, Thread, Message
        except ImportError:
            from models.domain import CalendarEvent, Thread, Message

        if CalendarEvent is not None and db.query(CalendarEvent).count() == 0:
            now_dt = datetime.utcnow()
            today_9am = now_dt.replace(hour=9, minute=0, second=0, microsecond=0)
            today_11am = now_dt.replace(hour=11, minute=30, second=0, microsecond=0)
            today_2pm = now_dt.replace(hour=14, minute=0, second=0, microsecond=0)

            sample_bookings = [
                CalendarEvent(
                    id=str(uuid.uuid4()),
                    summary="Full Body Relaxation Massage",
                    customer_phone="+61412345678",
                    start_time=today_9am,
                    end_time=today_9am + timedelta(minutes=60),
                    status="scheduled",
                    notes="Client requested essential oils.",
                ),
                CalendarEvent(
                    id=str(uuid.uuid4()),
                    summary="Deep Tissue Massage & Consultation",
                    customer_phone="+61498765432",
                    start_time=today_11am,
                    end_time=today_11am + timedelta(minutes=45),
                    status="scheduled",
                    notes="First time client, lower back pain.",
                ),
                CalendarEvent(
                    id=str(uuid.uuid4()),
                    summary="Nuru & Scalp Care Session",
                    customer_phone="+61455512345",
                    start_time=today_2pm,
                    end_time=today_2pm + timedelta(minutes=90),
                    status="scheduled",
                    notes="Prefers quiet session.",
                ),
            ]
            db.add_all(sample_bookings)

        if Thread is not None and Message is not None and db.query(Thread).count() == 0:
            sample_thread = Thread(
                id=str(uuid.uuid4()),
                customer_phone="+61412345678",
                state="auto-reply",
                priority="medium",
                sla_due_at=datetime.utcnow() + timedelta(hours=2),
                unread_count=0,
                auto_reply_enabled=True,
            )
            db.add(sample_thread)
            sample_msg = Message(
                id=str(uuid.uuid4()),
                thread_id=sample_thread.id,
                role="customer",
                text="Hi! I would like to book a relaxation massage session for today please.",
                at=datetime.utcnow(),
            )
            db.add(sample_msg)

        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional database session."""
    factory = _dyn_session_local()
    db = factory()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """Context manager yielding a transactional database session."""
    factory = _dyn_session_local()
    db = factory()
    try:
        yield db
    finally:
        db.close()


__all__ = [
    "Base",
    "engine",
    "SessionLocal",
    "get_db",
    "db_session",
    "init_db",
    "sqlite_connect_args",
    "set_sqlite_pragma",
]

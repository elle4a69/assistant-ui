from datetime import datetime, timedelta
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.core.single_line_migration import migrate_database_state, migrate_file_state
from backend.models.domain import Message, Thread


def test_second_line_thread_is_merged_without_losing_messages():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    primary = Thread(
        id="primary-thread",
        customer_phone="+61400000000",
        sms_account_key="primary",
        sla_due_at=now + timedelta(hours=1),
    )
    legacy = Thread(
        id="legacy-thread",
        customer_phone="+61400000000",
        sms_account_key="secondary",
        unread_count=2,
        sla_due_at=now + timedelta(hours=1),
    )
    session.add_all([primary, legacy])
    session.add(Message(id="legacy-message", thread_id=legacy.id, role="customer", text="Keep this", at=now))
    session.commit()

    migrate_database_state(session)

    threads = session.query(Thread).all()
    assert [thread.id for thread in threads] == ["primary-thread"]
    assert session.query(Message).one().thread_id == "primary-thread"
    assert primary.unread_count == 2


def test_second_line_services_are_discarded_when_state_is_consolidated(tmp_path):
    (tmp_path / "services.json").write_text(json.dumps([
        {"id": "line-one", "name": "Keep"},
        {"id": "line-two", "name": "Discard"},
    ]), encoding="utf-8")
    (tmp_path / "line_1_services.json").write_text(json.dumps([
        {"id": "line-one", "name": "Keep", "lineKey": "primary"},
    ]), encoding="utf-8")
    (tmp_path / "line_2_services.json").write_text(json.dumps([
        {"id": "line-two", "name": "Discard", "lineKey": "secondary"},
    ]), encoding="utf-8")

    migrate_file_state(str(tmp_path))

    assert json.loads((tmp_path / "services.json").read_text(encoding="utf-8")) == [
        {"id": "line-one", "name": "Keep"}
    ]
    assert not (tmp_path / "line_1_services.json").exists()
    assert not (tmp_path / "line_2_services.json").exists()

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from datetime import datetime

import main
from backend.curator.gaps import KnowledgeGap, KnowledgeGapManager
from backend.knowledge.models import Base
from backend.knowledge.repository import KnowledgeRepository
from backend.knowledge.retrieval import EmbeddingService
from backend.services import business_assistant_service as business


def make_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_gap(db, gap_id: str, account_key: str, question: str) -> KnowledgeGap:
    gap = KnowledgeGap(
        id=gap_id,
        tenant_id="default",
        account_key=account_key,
        canonical_question=question,
        owner_question=f"How should we answer: {question}",
        status="awaiting_owner",
        example_questions=[question],
    )
    db.add(gap)
    db.commit()
    return gap


def test_curator_question_history_edit_delete_and_undo_are_auditable_and_scoped():
    db = make_db()
    add_gap(db, "kg-primary", "primary", "Can I bring a friend?")
    add_gap(db, "kg-secondary", "secondary", "Where can I park?")
    manager = KnowledgeGapManager(db)

    edited = manager.update_gap(
        "kg-primary", "primary", {"owner_question": "Are companions allowed?"}, actor_id="settings-owner"
    )
    assert edited.owner_question == "Are companions allowed?"
    current, history = manager.history("kg-primary", "primary")
    assert history[0].action == "edit"
    assert history[0].snapshot["owner_question"] == "How should we answer: Can I bring a friend?"

    manager.delete_gap("kg-primary", "primary", actor_id="settings-owner")
    assert manager.get_gap("kg-primary").status == "deleted"
    restored = manager.undo_gap("kg-primary", "primary", actor_id="settings-owner")
    assert restored.status == "awaiting_owner"
    assert restored.owner_question == "Are companions allowed?"
    assert manager.history("kg-primary", "primary")[1][0].undone is True

    try:
        manager.history("kg-secondary", "primary")
        assert False, "cross-account lookup must fail"
    except ValueError as exc:
        assert "this account" in str(exc)
    db.close()


def test_business_assistant_lists_authoritative_questions_and_history_by_account(monkeypatch):
    db = make_db()
    add_gap(db, "kg-primary", "primary", "Can I bring a friend?")
    add_gap(db, "kg-secondary", "secondary", "Where can I park?")
    KnowledgeGapManager(db).update_gap(
        "kg-primary", "primary", {"owner_question": "Are companions allowed?"}, actor_id="owner"
    )
    monkeypatch.setattr(business, "get_knowledge_curator_state", lambda: {"proposals": []})

    result = business.execute_business_assistant_tool(db, "list_curator_questions", {
        "refresh": False, "limit": 10, "account_key": "primary", "include_history": True,
    })

    assert [item["id"] for item in result["questions"]] == ["kg-primary"]
    assert result["questions"][0]["history"][0]["state"]["owner_question"].startswith("How should")
    assert "customer_phones" not in result["questions"][0]
    assert "embedding" not in result["questions"][0]
    db.close()


def test_business_assistant_destructive_question_tools_require_exact_typed_phrase(monkeypatch):
    db = make_db()
    add_gap(db, "kg-primary", "primary", "Can I bring a friend?")

    pending = business.execute_business_assistant_tool(db, "delete_curator_question", {
        "question_id": "kg-primary", "account_key": "primary", "confirmation_phrase": "delete kg-primary",
    }, "yes")
    assert pending == {"status": "pending_confirmation", "confirmation_phrase": "delete kg-primary"}
    assert KnowledgeGapManager(db).get_gap("kg-primary").status == "awaiting_owner"

    deleted = business.execute_business_assistant_tool(db, "delete_curator_question", {
        "question_id": "kg-primary", "account_key": "primary", "confirmation_phrase": "delete kg-primary",
    }, "delete kg-primary")
    assert deleted["status"] == "deleted"
    assert deleted["history"][0]["action"] == "delete"
    db.close()


def test_delete_and_undo_resolved_question_safely_withdraws_and_restores_its_answer():
    db = make_db()
    add_gap(db, "kg-primary", "primary", "Can I bring a friend?")
    manager = KnowledgeGapManager(db, embedding_service=EmbeddingService(offline_only=True))
    record = manager.resolve_gap(
        "kg-primary", "Companions are allowed.", KnowledgeRepository(db), author_id="owner",
    )
    assert record.retrieval_enabled is True

    manager.delete_gap("kg-primary", "primary", actor_id="owner")
    assert KnowledgeRepository(db).get_by_id(record.id).retrieval_enabled is False
    restored = manager.undo_gap("kg-primary", "primary", actor_id="owner")
    assert restored.status == "resolved"
    assert restored.owner_answer == "Companions are allowed."
    restored_record = KnowledgeRepository(db).get_by_id(record.id)
    assert restored_record.status == "active"
    assert restored_record.retrieval_enabled is True
    db.close()


def test_curator_question_api_is_admin_protected_and_account_isolated(monkeypatch):
    db = make_db()
    add_gap(db, "kg-primary", "primary", "Can I bring a friend?")
    add_gap(db, "kg-secondary", "secondary", "Where can I park?")

    def override_db():
        yield db

    main.app.dependency_overrides[main.get_db] = override_db
    monkeypatch.setattr(main, "AUTH_PASSWORD", "curator-admin-password")
    client = TestClient(main.app)
    try:
        assert client.get("/api/settings/curator-questions?account_key=primary").status_code == 401
        expires = int(datetime.now().timestamp()) + 300
        client.cookies.set(main.AUTH_COOKIE_NAME, main._admin_session_token(expires))
        listed = client.get("/api/settings/curator-questions?account_key=primary")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["questions"]] == ["kg-primary"]
        assert client.get(
            "/api/settings/curator-questions/kg-secondary?account_key=primary"
        ).status_code == 404

        edited = client.patch(
            "/api/settings/curator-questions/kg-primary?account_key=primary",
            json={"owner_question": "Are companions allowed?"},
        )
        assert edited.status_code == 200
        assert edited.json()["history"][0]["state"]["owner_question"].startswith("How should")
        assert client.get(
            "/api/settings/curator-questions/kg-primary/versions/1?account_key=primary"
        ).json()["state"]["owner_question"].startswith("How should")
    finally:
        main.app.dependency_overrides.clear()
        db.close()

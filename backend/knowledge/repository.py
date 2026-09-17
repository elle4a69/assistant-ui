"""Knowledge repository abstraction for database persistence and JSONL synchronization."""

from datetime import datetime, timezone
import json
import os
import re
from typing import Any, List, Optional
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from backend.knowledge.models import (
    Base,
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeRecord,
    utcnow,
)


def _canonicalize_key(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return normalized[:160]


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        val = value.strip().casefold()
        if val in {"true", "1", "yes", "on"}:
            return True
        if val in {"false", "0", "no", "off", ""}:
            return False
    return default if value is None else bool(value)


class KnowledgeRepository:
    """Repository managing KnowledgeRecord, KnowledgeEvidence, and KnowledgeAlias entities."""

    def __init__(self, session_or_factory: Any):
        """Initialize repository with either a Session or sessionmaker factory.

        Args:
            session_or_factory: SQLAlchemy Session instance or sessionmaker factory.
        """
        if isinstance(session_or_factory, sessionmaker):
            self._session_factory = session_or_factory
            self._session = None
        elif isinstance(session_or_factory, Session):
            self._session = session_or_factory
            self._session_factory = None
        else:
            # Assume callable that produces a session
            self._session_factory = session_or_factory
            self._session = None

    def _get_session(self) -> Session:
        if self._session is not None:
            return self._session
        return self._session_factory()

    def get_by_id(self, record_id: str) -> Optional[KnowledgeRecord]:
        """Fetch a KnowledgeRecord by its primary key id."""
        session = self._get_session()
        try:
            return session.get(KnowledgeRecord, record_id)
        finally:
            if self._session is None:
                session.close()

    def list_active(
        self,
        account_key: str = "primary",
        include_shared: bool = True,
    ) -> List[KnowledgeRecord]:
        """List all active, retrieval-enabled KnowledgeRecords for an account key.

        Args:
            account_key: The SMS line or account key (e.g. 'primary', 'secondary').
            include_shared: If True, include records with account_key == 'shared'.
        """
        session = self._get_session()
        try:
            stmt = select(KnowledgeRecord).where(
                KnowledgeRecord.status == "active",
                KnowledgeRecord.retrieval_enabled == True,  # noqa: E712
            )
            if include_shared:
                stmt = stmt.where(
                    or_(
                        KnowledgeRecord.account_key == account_key,
                        KnowledgeRecord.account_key == "shared",
                    )
                )
            else:
                stmt = stmt.where(KnowledgeRecord.account_key == account_key)

            # Order predictably by canonical_key and updated_at desc
            stmt = stmt.order_by(KnowledgeRecord.canonical_key, KnowledgeRecord.updated_at.desc())
            return list(session.scalars(stmt).all())
        finally:
            if self._session is None:
                session.close()

    def save_record(
        self,
        record_data: dict,
        evidence_data: Optional[dict] = None,
    ) -> KnowledgeRecord:
        """Create or update a KnowledgeRecord and optionally attach KnowledgeEvidence."""
        session = self._get_session()
        close_on_exit = self._session is None
        try:
            data = dict(record_data)
            record_id = str(data.get("id") or "").strip()
            if not record_id:
                record_id = str(uuid.uuid4())

            existing = session.get(KnowledgeRecord, record_id)

            content = str(data.get("content") or data.get("text") or "").strip()
            normalised_content = str(
                data.get("normalised_content")
                or data.get("normalized_content")
                or content.casefold()
            ).strip()

            canonical_key = str(
                data.get("canonical_key")
                or data.get("key")
                or data.get("topic")
                or _canonicalize_key(content)
            ).strip()

            account_key = str(
                data.get("account_key")
                or data.get("sms_account_key")
                or data.get("scope")
                or "primary"
            ).strip().casefold()

            knowledge_type = str(
                data.get("knowledge_type")
                or data.get("type")
                or data.get("source_type")
                or "policy"
            ).strip()

            status = str(data.get("status") or "active").strip().casefold()
            retrieval_enabled = _to_bool(data.get("retrieval_enabled"), False)
            authority_level = str(data.get("authority_level") or "canonical_knowledge").strip()
            source_type = str(
                data.get("source_type") or data.get("type") or "manual_guidance"
            ).strip()
            source_actor_id = data.get("source_actor_id")
            source_id = data.get("source_id")
            confidence = float(data.get("confidence", 1.0))
            instruction = data.get("instruction") or data.get("applies_when")
            example_reply = data.get("example_reply") or data.get("customer_reply")

            effective_from = _parse_datetime(data.get("effective_from") or data.get("effectiveFrom"))
            effective_until = _parse_datetime(data.get("effective_until") or data.get("effectiveUntil"))
            supersedes_id = data.get("supersedes_id") or data.get("supersedesId")
            superseded_by_id = data.get("superseded_by_id") or data.get("supersededById")

            try:
                revision = int(data.get("revision") or data.get("version") or 1)
            except (ValueError, TypeError):
                revision = 1

            embedding = data.get("embedding")
            tenant_id = data.get("tenant_id") or "default"

            created_at = _parse_datetime(data.get("created_at")) or utcnow()
            updated_at = _parse_datetime(data.get("updated_at")) or utcnow()
            verified_at = _parse_datetime(data.get("verified_at"))

            if existing:
                existing.tenant_id = tenant_id
                existing.account_key = account_key
                existing.canonical_key = canonical_key
                existing.knowledge_type = knowledge_type
                existing.content = content
                existing.normalised_content = normalised_content
                existing.instruction = instruction
                existing.example_reply = example_reply
                existing.status = status
                existing.retrieval_enabled = retrieval_enabled
                existing.authority_level = authority_level
                existing.source_type = source_type
                existing.source_actor_id = source_actor_id
                existing.source_id = source_id
                existing.confidence = confidence
                existing.effective_from = effective_from
                existing.effective_until = effective_until
                existing.supersedes_id = supersedes_id
                existing.superseded_by_id = superseded_by_id
                existing.revision = revision
                if embedding is not None:
                    existing.embedding = embedding
                existing.updated_at = updated_at
                if verified_at is not None:
                    existing.verified_at = verified_at
                record = existing
            else:
                record = KnowledgeRecord(
                    id=record_id,
                    tenant_id=tenant_id,
                    account_key=account_key,
                    canonical_key=canonical_key,
                    knowledge_type=knowledge_type,
                    content=content,
                    normalised_content=normalised_content,
                    instruction=instruction,
                    example_reply=example_reply,
                    status=status,
                    retrieval_enabled=retrieval_enabled,
                    authority_level=authority_level,
                    source_type=source_type,
                    source_actor_id=source_actor_id,
                    source_id=source_id,
                    confidence=confidence,
                    effective_from=effective_from,
                    effective_until=effective_until,
                    supersedes_id=supersedes_id,
                    superseded_by_id=superseded_by_id,
                    revision=revision,
                    embedding=embedding,
                    created_at=created_at,
                    updated_at=updated_at,
                    verified_at=verified_at,
                )
                session.add(record)

            if evidence_data:
                ev_id = str(evidence_data.get("id") or uuid.uuid4())
                ev = KnowledgeEvidence(
                    id=ev_id,
                    knowledge_id=record.id,
                    source_type=str(evidence_data.get("source_type") or "manual_guidance"),
                    source_message_id=evidence_data.get("source_message_id"),
                    source_thread_id=evidence_data.get("source_thread_id"),
                    source_actor_id=evidence_data.get("source_actor_id"),
                    original_text_reference=evidence_data.get("original_text_reference"),
                    created_at=_parse_datetime(evidence_data.get("created_at")) or utcnow(),
                )
                session.add(ev)

            session.commit()
            session.refresh(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            if close_on_exit:
                session.close()

    def mark_superseded(self, predecessor_id: str, successor_id: str) -> None:
        """Mark a predecessor record as superseded by a successor record.

        Updates both predecessor (`status="superseded"`, `superseded_by_id=successor_id`)
        and successor (`supersedes_id=predecessor_id`).
        """
        session = self._get_session()
        close_on_exit = self._session is None
        try:
            predecessor = session.get(KnowledgeRecord, predecessor_id)
            successor = session.get(KnowledgeRecord, successor_id)

            if predecessor:
                predecessor.status = "superseded"
                predecessor.superseded_by_id = successor_id
                predecessor.updated_at = utcnow()

            if successor:
                successor.supersedes_id = predecessor_id
                successor.updated_at = utcnow()

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            if close_on_exit:
                session.close()

    def add_alias(
        self,
        knowledge_id: str,
        utterance: str,
        source: str = "manual",
    ) -> KnowledgeAlias:
        """Add a user question variant or utterance alias for a knowledge record."""
        session = self._get_session()
        close_on_exit = self._session is None
        try:
            cleaned = str(utterance or "").strip()
            if not cleaned:
                raise ValueError("Utterance cannot be empty.")

            alias = KnowledgeAlias(
                id=str(uuid.uuid4()),
                knowledge_id=knowledge_id,
                utterance=cleaned,
                normalised_utterance=cleaned.casefold(),
                source=source,
                usage_count=0,
                created_at=utcnow(),
            )
            session.add(alias)
            session.commit()
            session.refresh(alias)
            return alias
        except Exception:
            session.rollback()
            raise
        finally:
            if close_on_exit:
                session.close()

    def get_aliases(self, knowledge_id: str) -> List[KnowledgeAlias]:
        """Fetch all utterance aliases for a knowledge record."""
        session = self._get_session()
        try:
            stmt = (
                select(KnowledgeAlias)
                .where(KnowledgeAlias.knowledge_id == knowledge_id)
                .order_by(KnowledgeAlias.created_at.asc())
            )
            return list(session.scalars(stmt).all())
        finally:
            if self._session is None:
                session.close()

    def list_all(self) -> List[KnowledgeRecord]:
        """Fetch all KnowledgeRecords ordered by canonical_key and created_at asc."""
        session = self._get_session()
        try:
            stmt = select(KnowledgeRecord).order_by(
                KnowledgeRecord.canonical_key, KnowledgeRecord.created_at.asc()
            )
            return list(session.scalars(stmt).all())
        finally:
            if self._session is None:
                session.close()

    def sync_from_jsonl(self, jsonl_path: str) -> int:
        """One-way sync / population of records from a JSONL file (e.g. learned_information.jsonl).

        Returns the number of imported/updated records.
        """
        if not os.path.exists(jsonl_path):
            return 0

        count = 0
        with open(jsonl_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    raw = json.loads(line_str)
                    if not isinstance(raw, dict):
                        continue

                    content = str(
                        raw.get("text")
                        or raw.get("content")
                        or raw.get("answer")
                        or raw.get("owner_information")
                        or raw.get("question")
                        or ""
                    ).strip()

                    if not content:
                        continue

                    # Determine record ID deterministically if absent
                    record_id = str(raw.get("id") or raw.get("record_id") or "").strip()
                    if not record_id:
                        record_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"assistant-ui:jsonl:{line_str}"))

                    canonical_key = str(
                        raw.get("canonical_key")
                        or raw.get("key")
                        or raw.get("topic")
                        or _canonicalize_key(content)
                    ).strip()

                    account_key = str(
                        raw.get("sms_account_key")
                        or raw.get("account_key")
                        or raw.get("scope")
                        or "primary"
                    ).strip().casefold()
                    if account_key not in {"primary", "secondary", "shared"}:
                        account_key = "primary"

                    knowledge_type = str(
                        raw.get("knowledge_type")
                        or raw.get("type")
                        or raw.get("source_type")
                        or "policy"
                    ).strip()

                    status = str(raw.get("status") or "active").strip().casefold()
                    retrieval_enabled = _to_bool(raw.get("retrieval_enabled"), False)
                    authority_level = str(
                        raw.get("authority_level")
                        or ("canonical_knowledge" if retrieval_enabled else "historical_response")
                    ).strip()

                    record_data = {
                        "id": record_id,
                        "tenant_id": raw.get("tenant_id") or "default",
                        "account_key": account_key,
                        "canonical_key": canonical_key,
                        "knowledge_type": knowledge_type,
                        "content": content,
                        "normalised_content": str(raw.get("normalised_content") or content.casefold()).strip(),
                        "instruction": raw.get("instruction") or raw.get("applies_when"),
                        "example_reply": raw.get("example_reply") or raw.get("customer_reply"),
                        "status": status,
                        "retrieval_enabled": retrieval_enabled,
                        "authority_level": authority_level,
                        "source_type": str(raw.get("source_type") or raw.get("type") or "learned_jsonl").strip(),
                        "source_actor_id": raw.get("source_actor_id"),
                        "source_id": raw.get("source_id"),
                        "confidence": float(raw.get("confidence", 1.0)),
                        "effective_from": raw.get("effective_from") or raw.get("effectiveFrom"),
                        "effective_until": raw.get("effective_until") or raw.get("effectiveUntil"),
                        "supersedes_id": raw.get("supersedes_id") or raw.get("supersedesId"),
                        "superseded_by_id": raw.get("superseded_by_id") or raw.get("supersededById"),
                        "revision": int(raw.get("revision") or raw.get("version") or 1),
                        "created_at": raw.get("created_at"),
                        "updated_at": raw.get("updated_at"),
                        "verified_at": raw.get("verified_at"),
                    }

                    # Create evidence entry if source info present
                    evidence_data = None
                    if raw.get("question") or raw.get("owner_information") or raw.get("source_message_id"):
                        evidence_data = {
                            "source_type": str(raw.get("source_type") or "learned_jsonl"),
                            "source_message_id": raw.get("source_message_id"),
                            "source_thread_id": raw.get("source_thread_id"),
                            "source_actor_id": raw.get("source_actor_id"),
                            "original_text_reference": raw.get("question") or raw.get("owner_information"),
                        }

                    self.save_record(record_data, evidence_data=evidence_data)
                    count += 1
                except Exception as ex:
                    # Robust against individual line errors
                    print(f"Error syncing record from JSONL: {ex}")
                    continue

        return count

    def export_to_jsonl(self, jsonl_path: str) -> int:
        """Export all KnowledgeRecords to a JSONL file.

        Dumps records back into JSONL format for parity and backup.
        Returns the number of exported records.
        """
        session = self._get_session()
        try:
            stmt = select(KnowledgeRecord).order_by(
                KnowledgeRecord.canonical_key, KnowledgeRecord.created_at.asc()
            )
            records = session.scalars(stmt).all()

            os.makedirs(os.path.dirname(os.path.abspath(jsonl_path)), exist_ok=True)
            tmp_path = f"{jsonl_path}.{uuid.uuid4().hex}.tmp"
            count = 0
            with open(tmp_path, "w", encoding="utf-8") as handle:
                for r in records:
                    doc = {
                        "id": r.id,
                        "canonical_key": r.canonical_key,
                        "account_key": r.account_key,
                        "sms_account_key": r.account_key,
                        "scope": r.account_key,
                        "knowledge_type": r.knowledge_type,
                        "type": r.knowledge_type,
                        "content": r.content,
                        "text": r.content,
                        "normalised_content": r.normalised_content,
                        "instruction": r.instruction,
                        "applies_when": r.instruction,
                        "example_reply": r.example_reply,
                        "customer_reply": r.example_reply,
                        "status": r.status,
                        "retrieval_enabled": r.retrieval_enabled,
                        "authority_level": r.authority_level,
                        "source_type": r.source_type,
                        "source_actor_id": r.source_actor_id,
                        "source_id": r.source_id,
                        "confidence": r.confidence,
                        "effective_from": r.effective_from.isoformat() if r.effective_from else None,
                        "effective_until": r.effective_until.isoformat() if r.effective_until else None,
                        "supersedes_id": r.supersedes_id,
                        "superseded_by_id": r.superseded_by_id,
                        "revision": r.revision,
                        "version": r.revision,
                        "tenant_id": r.tenant_id,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                        "verified_at": r.verified_at.isoformat() if r.verified_at else None,
                    }
                    handle.write(json.dumps(doc, ensure_ascii=False) + "\n")
                    count += 1

            os.replace(tmp_path, jsonl_path)
            return count
        finally:
            if self._session is None:
                session.close()

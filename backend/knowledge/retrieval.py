"""Semantic Candidate Discovery and Hybrid Retrieval subsystem.

Governing Principles:
1. Embeddings locate candidates. Embeddings do not determine authority.
2. Similarity scores never trigger auto-merging.
3. Fail-closed conflict handling: conflicting candidates result in no authoritative fact.
4. Separation of concerns: authoritative knowledge retrieval vs style few-shot pairs.
"""

from datetime import datetime, timezone
import hashlib
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select

from backend.knowledge.models import (
    KnowledgeAlias,
    KnowledgeRecord,
    utcnow,
)
from backend.knowledge.repository import KnowledgeRepository

# Authority level hierarchy mapping: higher value = higher authority
AUTHORITY_HIERARCHY: Dict[str, int] = {
    "owner_instruction": 7,
    "system_truth": 6,
    "staff_instruction": 5,
    "canonical_knowledge": 4,
    "historical_response": 3,
    "customer_assertion": 2,
    "ai_inference": 1,
}


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two float vectors.

    Returns 0.0 if either vector is empty, lengths mismatch, or either norm is zero.
    Result is bounded between -1.0 and 1.0.
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    sim = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    if not math.isfinite(sim):
        return 0.0
    return max(-1.0, min(1.0, float(sim)))


def _tokenize(text: str) -> list[str]:
    """Tokenize string into lowercase alphanumeric words."""
    return re.findall(r"\w+", (text or "").casefold())


def _lexical_similarity(query: str, target: str) -> float:
    """Compute lexical overlap score between query and target text.

    Combines exact/substring containment, token overlap ratio, and Jaccard similarity.
    """
    q_norm = (query or "").strip().casefold()
    t_norm = (target or "").strip().casefold()
    if not q_norm or not t_norm:
        return 0.0

    if q_norm == t_norm:
        return 1.0
    if q_norm in t_norm:
        return min(1.0, 0.7 + 0.3 * (len(q_norm) / len(t_norm)))

    q_tokens = set(_tokenize(q_norm))
    t_tokens = set(_tokenize(t_norm))
    if not q_tokens or not t_tokens:
        return 0.0

    intersection = q_tokens & t_tokens
    if not intersection:
        return 0.0

    overlap_ratio = len(intersection) / len(q_tokens)
    union = q_tokens | t_tokens
    jaccard = len(intersection) / len(union) if union else 0.0

    return 0.7 * overlap_ratio + 0.3 * jaccard


def _is_within_effective_dates(record: KnowledgeRecord, now: Optional[datetime] = None) -> bool:
    """Check if record is within its effective_from and effective_until date window."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if record.effective_from:
        ef = record.effective_from
        if ef.tzinfo is None:
            ef = ef.replace(tzinfo=timezone.utc)
        if current < ef:
            return False

    if record.effective_until:
        eu = record.effective_until
        if eu.tzinfo is None:
            eu = eu.replace(tzinfo=timezone.utc)
        if current > eu:
            return False

    return True


class EmbeddingService:
    """Service providing vector embeddings with OpenAI integration and deterministic offline fallback."""

    def __init__(
        self,
        client: Optional[Any] = None,
        model: Optional[str] = None,
        dim: int = 1536,
        offline_only: bool = False,
    ):
        self.offline_only = offline_only
        self.model = model or ("text-embedding-3-small" if offline_only else (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small"))
        self.dim = dim
        self._client = client
        self._client_resolved = client is not None or offline_only

    def _resolve_client(self) -> Optional[Any]:
        if self.offline_only:
            return None
        if self._client_resolved:
            return self._client
        self._client_resolved = True
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key)
            return self._client
        except Exception:
            return None

    def get_embedding(self, text: str) -> list[float]:
        """Compute float embedding vector for given text.

        Calls OpenAI embeddings API when available, falling back gracefully to
        a deterministic feature-hashed pseudo-embedding when offline or unconfigured.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return [0.0] * self.dim

        if self.offline_only:
            return self._deterministic_pseudo_embedding(clean_text)

        client = self._resolve_client()
        if client is not None:
            try:
                resp = client.embeddings.create(input=clean_text, model=self.model)
                if resp and resp.data and len(resp.data) > 0:
                    return list(resp.data[0].embedding)
            except Exception:
                # Graceful offline fallback on API failure or network absence
                pass

        return self._deterministic_pseudo_embedding(clean_text)

    def _deterministic_pseudo_embedding(self, text: str) -> list[float]:
        """Generate deterministic, unit-normalized feature-hashed pseudo-embedding.

        Guarantees:
        1. Fully offline and deterministic (no random seed or network call).
        2. Fixed dimension (matching self.dim, default 1536).
        3. Unit L2 norm (sum of squares == 1.0).
        4. Identical strings yield cosine similarity == 1.0.
        5. Token overlap yields positive cosine similarity.
        """
        words = _tokenize(text)
        if not words:
            return [0.0] * self.dim

        vec = [0.0] * self.dim

        # Feature hashing of unigrams, subword character n-grams, and bigrams
        features: list[str] = []
        for w in words:
            features.append(w)
            if len(w) >= 3:
                for n in range(3, min(6, len(w) + 1)):
                    for i in range(len(w) - n + 1):
                        features.append(w[i : i + n])
        for i in range(len(words) - 1):
            features.append(f"{words[i]}_{words[i+1]}")

        for feat in features:
            h = hashlib.sha256(feat.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if (h[4] & 1) == 0 else -1.0
            vec[idx] += sign

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-12:
            return [x / norm for x in vec]
        return vec

    @staticmethod
    def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        return cosine_similarity(vec_a, vec_b)


class HybridKnowledgeRetriever:
    """Hybrid retrieval engine combining lexical matching, vector search, and authority filtering."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self.repository = repository
        self.embedding_service = embedding_service or EmbeddingService()

    def find_candidates(
        self,
        query: str,
        account_key: str = "primary",
        tenant_id: str = "default",
        limit: int = 5,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """Gather and rank knowledge candidates using vector similarity and lexical matching.

        Ranks candidates strictly by:
        1. Tenant & account eligibility (account_key in {record.account_key, "shared"})
        2. Current validity (retrieval_enabled == True, status == "active", within effective dates)
        3. Authority level (owner_instruction > system_truth > staff_instruction > canonical_knowledge >
           historical_response > customer_assertion > ai_inference)
        4. Semantic similarity + lexical overlap
        """
        query_text = (query or "").strip()
        if not query_text:
            return []

        query_vec = self.embedding_service.get_embedding(query_text)
        current_time = now or datetime.now(timezone.utc)

        session = self.repository._get_session()
        close_on_exit = self.repository._session is None
        try:
            records = list(session.scalars(select(KnowledgeRecord)).all())
            aliases = list(session.scalars(select(KnowledgeAlias)).all())
            if close_on_exit:
                session.expunge_all()
        finally:
            if close_on_exit:
                session.close()

        aliases_by_record: dict[str, list[KnowledgeAlias]] = {}
        for alias in aliases:
            aliases_by_record.setdefault(alias.knowledge_id, []).append(alias)

        candidate_list: list[dict] = []

        for record in records:
            # 1. Tenant & Account Eligibility
            is_tenant_match = (
                tenant_id == "default"
                or record.tenant_id in {tenant_id, None, "default"}
            )
            is_account_match = record.account_key in {account_key, "shared"}
            is_eligible = is_tenant_match and is_account_match

            # 2. Current Validity
            is_valid = (
                record.retrieval_enabled is True
                and record.status == "active"
                and _is_within_effective_dates(record, current_time)
            )

            # 3. Authority Rank
            authority_rank = AUTHORITY_HIERARCHY.get(record.authority_level, 0)

            # 4. Semantic similarity & lexical score
            rec_emb = record.embedding
            if rec_emb is None:
                rec_emb = self.embedding_service.get_embedding(record.content)
            rec_sim = max(0.0, cosine_similarity(query_vec, rec_emb))

            rec_lex = max(
                _lexical_similarity(query_text, record.content),
                _lexical_similarity(query_text, record.canonical_key.replace("-", " ")),
                _lexical_similarity(query_text, record.instruction or ""),
            )

            matched_by = "content"
            best_sim = rec_sim
            best_lex = rec_lex

            # Check aliases
            rec_aliases = aliases_by_record.get(record.id, [])
            for alias in rec_aliases:
                alias_emb = alias.embedding
                if alias_emb is None:
                    alias_emb = self.embedding_service.get_embedding(alias.utterance)
                alias_sim = max(0.0, cosine_similarity(query_vec, alias_emb))
                alias_lex = _lexical_similarity(query_text, alias.utterance)

                alias_combined = 0.6 * alias_sim + 0.4 * alias_lex
                current_combined = 0.6 * best_sim + 0.4 * best_lex
                if alias_combined > current_combined:
                    best_sim = alias_sim
                    best_lex = alias_lex
                    matched_by = f"alias:{alias.utterance}"

            combined_score = 0.6 * best_sim + 0.4 * best_lex

            # Relevance filter: candidate must exhibit non-trivial relevance
            if combined_score <= 0.05 and best_sim <= 0.1 and best_lex <= 0.1:
                continue

            cand_dict = record.to_dict()
            cand_dict.update({
                "text": record.content,
                "sms_account_key": record.account_key,
                "scope": record.account_key,
                "review_status": "approved",
                "score": round(combined_score, 4),
                "semantic_similarity": round(best_sim, 4),
                "lexical_score": round(best_lex, 4),
                "matched_by": matched_by,
                "authority_rank": authority_rank,
                "is_eligible": bool(is_eligible),
                "is_valid": bool(is_valid),
            })
            candidate_list.append(cand_dict)

        # Sort strictly by:
        # 1. is_eligible (descending)
        # 2. is_valid (descending)
        # 3. authority_rank (descending)
        # 4. score (descending)
        # 5. revision (descending)
        # 6. updated_at (descending)
        # 7. id
        candidate_list.sort(
            key=lambda c: (
                1 if c["is_eligible"] else 0,
                1 if c["is_valid"] else 0,
                c["authority_rank"],
                c["score"],
                c.get("revision", 1),
                c.get("updated_at") or "",
                c.get("id") or "",
            ),
            reverse=True,
        )

        return candidate_list[:limit]

    def retrieve_authoritative_fact(
        self,
        query: str,
        account_key: str = "primary",
        tenant_id: str = "default",
        now: Optional[datetime] = None,
    ) -> Optional[dict]:
        """Retrieve the single deterministic winning record or None if absent or conflicted.

        Process:
        1. Gathers candidate records matching the query.
        2. Expands lineage (revision chain and canonical_key records) so authority resolution
           has full visibility into predecessors, successors, and competing variants.
        3. Evaluates authority deterministically via resolve_knowledge_authority with fail-closed
           conflict handling.
        4. Returns the single deterministic winning record or None if conflicted or invalid.
        """
        candidates = self.find_candidates(
            query=query,
            account_key=account_key,
            tenant_id=tenant_id,
            limit=10,
            now=now,
        )
        if not candidates:
            return None

        # Gather all related records for candidate canonical keys to provide full lineage
        candidate_keys = {c["canonical_key"] for c in candidates if c.get("canonical_key")}
        candidate_ids = {c["id"] for c in candidates if c.get("id")}

        session = self.repository._get_session()
        close_on_exit = self.repository._session is None
        all_related_records: list[KnowledgeRecord] = []
        try:
            predecessor_ids = [c.get("supersedes_id") for c in candidates if c.get("supersedes_id")]
            conditions = [KnowledgeRecord.canonical_key.in_(candidate_keys)]
            if candidate_ids:
                conditions.append(KnowledgeRecord.supersedes_id.in_(candidate_ids))
            if predecessor_ids:
                conditions.append(KnowledgeRecord.id.in_(predecessor_ids))

            stmt = select(KnowledgeRecord).where(or_(*conditions))
            all_related_records = list(session.scalars(stmt).all())
            if close_on_exit:
                session.expunge_all()
        finally:
            if close_on_exit:
                session.close()

        records_to_resolve_by_id: dict[str, dict] = {}
        for c in candidates:
            records_to_resolve_by_id[c["id"]] = c

        for r in all_related_records:
            if r.id not in records_to_resolve_by_id:
                d = r.to_dict()
                d.update({
                    "text": r.content,
                    "sms_account_key": r.account_key,
                    "scope": r.account_key,
                    "review_status": "approved",
                })
                records_to_resolve_by_id[r.id] = d

        from backend.curator.authority import resolve_knowledge_authority

        resolved = resolve_knowledge_authority(
            records=list(records_to_resolve_by_id.values()),
            account_key=account_key,
            now=now,
        )

        if not resolved:
            return None

        # Top candidate represents primary topic intent
        top_cand = candidates[0]
        top_cand_key = top_cand.get("canonical_key")

        if top_cand_key:
            matching_key_records = [
                r for r in resolved
                if r.get("canonical_key") == top_cand_key
            ]
            if not matching_key_records:
                # Top candidate key was present in candidate set, but no record survived in resolved.
                # Check whether multiple differing candidate texts existed for that key (conflict)
                # or if eligible/valid candidates existed that failed authority resolution.
                key_records = [
                    r for r in records_to_resolve_by_id.values()
                    if r.get("canonical_key") == top_cand_key
                ]
                key_texts = {
                    str(r.get("text") or r.get("content") or "").strip()
                    for r in key_records
                    if str(r.get("text") or r.get("content") or "").strip()
                }
                had_eligible = any(
                    c.get("is_eligible", True) and c.get("is_valid", True)
                    for c in candidates
                    if c.get("canonical_key") == top_cand_key
                )
                if len(key_texts) > 1 or had_eligible:
                    # Primary topic intent suffered a conflict or invalidation - fail closed
                    return None
            pool = matching_key_records
        else:
            pool = resolved

        if not pool:
            return None

        # Deterministic winner selection among resolved records
        winner = max(
            pool,
            key=lambda r: (
                AUTHORITY_HIERARCHY.get(r.get("authority_level", ""), 0),
                records_to_resolve_by_id.get(r.get("id", ""), {}).get("score", 0.0),
                r.get("revision", 1),
                r.get("updated_at") or "",
                r.get("id") or "",
            ),
        )
        return winner


def find_similar_knowledge_records(
    text: str,
    repository: KnowledgeRepository,
    threshold: float = 0.75,
    limit: int = 5,
    embedding_service: Optional[EmbeddingService] = None,
) -> list[tuple[KnowledgeRecord, float]]:
    """Discover similar knowledge records for curator duplicate and conflict detection.

    Returns list of (KnowledgeRecord, similarity_score) tuples where similarity_score >= threshold,
    ordered by similarity_score descending, up to limit.
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return []

    emb_svc = embedding_service or EmbeddingService()
    text_vec = emb_svc.get_embedding(clean_text)

    session = repository._get_session()
    close_on_exit = repository._session is None
    try:
        records = list(session.scalars(select(KnowledgeRecord)).all())
        aliases = list(session.scalars(select(KnowledgeAlias)).all())
        if close_on_exit:
            session.expunge_all()
    finally:
        if close_on_exit:
            session.close()

    aliases_by_record: dict[str, list[KnowledgeAlias]] = {}
    for alias in aliases:
        aliases_by_record.setdefault(alias.knowledge_id, []).append(alias)

    results: list[tuple[KnowledgeRecord, float]] = []

    for record in records:
        rec_emb = record.embedding
        if rec_emb is None:
            rec_emb = emb_svc.get_embedding(record.content)
        sim = max(0.0, cosine_similarity(text_vec, rec_emb))

        for alias in aliases_by_record.get(record.id, []):
            alias_emb = alias.embedding
            if alias_emb is None:
                alias_emb = emb_svc.get_embedding(alias.utterance)
            alias_sim = max(0.0, cosine_similarity(text_vec, alias_emb))
            if alias_sim > sim:
                sim = alias_sim

        if sim >= threshold:
            results.append((record, round(sim, 4)))

    results.sort(key=lambda item: item[1], reverse=True)
    return results[:limit]

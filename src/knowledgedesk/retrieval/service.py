import secrets
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from knowledgedesk.persistence.models import (
    AccessGroup,
    Chunk,
    Document,
    DocumentGroup,
    DocumentIndex,
    DocumentVersion,
    GroupMembership,
    IndexGeneration,
    KnowledgeBase,
    Membership,
)
from knowledgedesk.retrieval.ranking import RankedItem, reciprocal_rank_fusion


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: Chunk
    document: Document
    version: DocumentVersion
    score: float
    evidence_id: str

    def model_evidence(self) -> dict[str, str]:
        return {"evidence_id": self.evidence_id, "text": self.chunk.content}


def _authorized_statement(*, user_id: UUID, workspace_id: UUID, base_id: UUID) -> Any:
    member = exists(
        select(Membership.id).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )
    group_member = exists(
        select(GroupMembership.id)
        .join(AccessGroup, AccessGroup.id == GroupMembership.group_id)
        .join(DocumentGroup, DocumentGroup.group_id == AccessGroup.id)
        .where(
            DocumentGroup.document_id == Document.id,
            GroupMembership.user_id == user_id,
            AccessGroup.workspace_id == workspace_id,
        )
    )
    return (
        select(Chunk, Document, DocumentVersion)
        .join(DocumentIndex, DocumentIndex.id == Chunk.document_index_id)
        .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(IndexGeneration, IndexGeneration.id == Chunk.generation_id)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .where(
            member,
            KnowledgeBase.id == base_id,
            KnowledgeBase.workspace_id == workspace_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
            Document.revoked_at.is_(None),
            Document.active_version_id == DocumentVersion.id,
            DocumentVersion.state == "ready",
            or_(DocumentVersion.valid_from.is_(None), DocumentVersion.valid_from <= func.now()),
            or_(DocumentVersion.valid_until.is_(None), DocumentVersion.valid_until > func.now()),
            DocumentIndex.state == "ready",
            IndexGeneration.state == "active",
            or_(Document.public_to_workspace.is_(True), group_member),
        )
    )


def _hits(rows: Any) -> list[SearchHit]:
    hits = []
    for row in rows:
        chunk, document, version, score = row
        hits.append(
            SearchHit(
                chunk=chunk,
                document=document,
                version=version,
                score=float(score),
                evidence_id=secrets.token_urlsafe(12),
            )
        )
    return hits


def lexical_search(
    session: Session,
    *,
    user_id: UUID,
    workspace_id: UUID,
    base_id: UUID,
    query: str,
    limit: int = 20,
) -> list[SearchHit]:
    if not query.strip():
        return []
    statement = _authorized_statement(user_id=user_id, workspace_id=workspace_id, base_id=base_id)
    ts_query = func.plainto_tsquery("simple", query)
    rank = func.ts_rank_cd(Chunk.search_vector, ts_query)
    statement = statement.add_columns(rank.label("rank")).where(
        or_(Chunk.search_vector.op("@@")(ts_query), Chunk.content.icontains(query, autoescape=True))
    )
    rows = session.execute(statement.order_by(rank.desc(), Chunk.id).limit(limit)).all()
    return _hits(rows)


def vector_search(
    session: Session,
    *,
    user_id: UUID,
    workspace_id: UUID,
    base_id: UUID,
    query_vector: list[float],
    model_name: str,
    limit: int = 20,
) -> list[SearchHit]:
    if not query_vector or any(not isinstance(value, (int, float)) for value in query_vector):
        raise ValueError("query vector must contain numeric values")
    statement = _authorized_statement(user_id=user_id, workspace_id=workspace_id, base_id=base_id)
    distance = Chunk.embedding.cosine_distance(query_vector)
    statement = statement.add_columns((1 - distance).label("similarity")).where(
        Chunk.embedding.is_not(None),
        IndexGeneration.embedding_model == model_name,
        IndexGeneration.embedding_dimensions == len(query_vector),
    )
    rows = session.execute(statement.order_by(distance, Chunk.id).limit(limit)).all()
    return _hits(rows)


def hybrid_search(
    lexical: list[SearchHit], vector: list[SearchHit], *, limit: int
) -> list[SearchHit]:
    candidates: dict[Hashable, SearchHit] = {hit.chunk.id: hit for hit in [*lexical, *vector]}
    fused = reciprocal_rank_fusion(
        [
            [RankedItem(hit.chunk.id, hit) for hit in lexical],
            [RankedItem(hit.chunk.id, hit) for hit in vector],
        ],
        limit=limit,
    )
    return [candidates[identifier] for identifier, _, _ in fused]


def revalidate_hits(
    session: Session,
    *,
    user_id: UUID,
    workspace_id: UUID,
    base_id: UUID,
    hits: list[SearchHit],
) -> list[SearchHit]:
    if not hits:
        return []
    ids = {hit.chunk.id for hit in hits}
    statement = _authorized_statement(user_id=user_id, workspace_id=workspace_id, base_id=base_id)
    rows = session.execute(statement.where(Chunk.id.in_(ids))).all()
    current = {row[0].id: row for row in rows}
    if len(current) != len(ids):
        return []
    for previous in hits:
        row = current[previous.chunk.id]
        if (
            row[1].security_epoch != previous.document.security_epoch
            or row[1].active_version_id != previous.document.active_version_id
            or row[2].id != previous.version.id
        ):
            return []
    return [
        SearchHit(
            chunk=current[hit.chunk.id][0],
            document=current[hit.chunk.id][1],
            version=current[hit.chunk.id][2],
            score=hit.score,
            evidence_id=hit.evidence_id,
        )
        for hit in hits
    ]


def build_model_context(
    hits: list[SearchHit], *, max_characters: int, per_document: int = 2
) -> list[dict[str, str]]:
    context: list[dict[str, str]] = []
    counts: dict[UUID, int] = {}
    source_groups: dict[UUID, str] = {}
    used = 0
    for hit in hits:
        count = counts.get(hit.document.id, 0)
        if count >= per_document:
            continue
        remaining = max_characters - used
        if remaining <= 0:
            break
        text = hit.chunk.content
        if len(text) > remaining:
            continue
        source_group = source_groups.setdefault(hit.document.id, secrets.token_urlsafe(8))
        context.append(
            {
                "evidence_id": hit.evidence_id,
                "source_group_id": source_group,
                "text": text,
            }
        )
        used += len(text)
        counts[hit.document.id] = count + 1
    return context

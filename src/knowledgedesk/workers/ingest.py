from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select

from knowledgedesk.ingestion.chunking import chunk_blocks
from knowledgedesk.ingestion.parsers import (
    DocumentLimitExceeded,
    UnsupportedDocument,
    parse_document,
)
from knowledgedesk.persistence.database import SessionFactory
from knowledgedesk.persistence.models import (
    AuditEvent,
    Chunk,
    Document,
    DocumentIndex,
    DocumentVersion,
    ExtractionBlock,
    IndexGeneration,
    IngestionJob,
)
from knowledgedesk.providers.ollama import OllamaProvider, ProviderUnavailable
from knowledgedesk.settings import get_settings

logger = logging.getLogger("knowledgedesk.worker")
MAX_ATTEMPTS = 3
LEASE_DURATION = timedelta(minutes=10)
RETRY_DELAYS = (5, 30, 120)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    document_id: UUID
    version_id: UUID
    generation_id: UUID
    job_epoch: int
    storage_key: str
    document_epoch: int
    deleted_at: datetime | None
    revoked_at: datetime | None
    active_version_id: UUID | None
    embedding_model: str | None
    expected_dimensions: int | None
    workspace_id: UUID
    base_id: UUID


def _claim(job_id: UUID | None = None) -> UUID | None:
    now = datetime.now(UTC)
    retry_eligible = or_(
        IngestionJob.attempts == 0,
        and_(
            IngestionJob.attempts == 1,
            IngestionJob.updated_at <= now - timedelta(seconds=RETRY_DELAYS[0]),
        ),
        and_(
            IngestionJob.attempts == 2,
            IngestionJob.updated_at <= now - timedelta(seconds=RETRY_DELAYS[1]),
        ),
    )
    with SessionFactory.begin() as session:
        statement = (
            select(IngestionJob)
            .where(
                IngestionJob.state == "queued",
                IngestionJob.attempts < MAX_ATTEMPTS,
                retry_eligible,
            )
            .order_by(IngestionJob.created_at, IngestionJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job_id is not None:
            statement = statement.where(IngestionJob.id == job_id)
        job = session.scalar(statement)
        if job is None:
            return None
        job.state = "running"
        job.attempts += 1
        job.lease_until = now + LEASE_DURATION
        job.updated_at = now
        return job.id


def _load_claimed(job_id: UUID) -> ClaimedJob | None:
    with SessionFactory() as session:
        job = session.get(IngestionJob, job_id)
        if job is None or job.state != "running":
            return None
        document = session.get(Document, job.document_id)
        version = session.get(DocumentVersion, job.version_id)
        generation = session.get(IndexGeneration, job.generation_id)
        if document is None or version is None or generation is None:
            return None
        return ClaimedJob(
            job_id=job.id,
            document_id=job.document_id,
            version_id=job.version_id,
            generation_id=job.generation_id,
            job_epoch=job.security_epoch,
            storage_key=version.storage_key or document.storage_key,
            document_epoch=document.security_epoch,
            deleted_at=document.deleted_at,
            revoked_at=document.revoked_at,
            active_version_id=document.active_version_id,
            embedding_model=generation.embedding_model,
            expected_dimensions=generation.embedding_dimensions,
            workspace_id=document.workspace_id,
            base_id=document.knowledge_base_id,
        )


def _mark_failure(job_id: UUID, code: str, *, retryable: bool) -> None:
    now = datetime.now(UTC)
    with SessionFactory.begin() as session:
        job = session.scalar(
            select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
        )
        if job is None:
            return
        job.lease_until = None
        job.updated_at = now
        job.error_code = code
        job.state = "queued" if retryable and job.attempts < MAX_ATTEMPTS else "failed"
        if job.state == "failed":
            version = session.get(DocumentVersion, job.version_id)
            if version is not None and version.state != "ready":
                version.state = "failed"
                version.error_code = code


def process_one(job_id: UUID | None = None) -> bool:
    claimed_id = _claim(job_id)
    if claimed_id is None:
        return False
    claimed = _load_claimed(claimed_id)
    if claimed is None:
        _mark_failure(claimed_id, "job_references_missing_data", retryable=False)
        return True
    current_job_id = claimed.job_id
    document_id = claimed.document_id
    version_id = claimed.version_id
    generation_id = claimed.generation_id
    job_epoch = claimed.job_epoch
    storage_key = claimed.storage_key
    observed_epoch = claimed.document_epoch
    deleted_at = claimed.deleted_at
    revoked_at = claimed.revoked_at
    embedding_model = claimed.embedding_model
    expected_dimensions = claimed.expected_dimensions
    workspace_id = claimed.workspace_id
    settings = get_settings()
    if deleted_at is not None or revoked_at is not None or job_epoch != observed_epoch:
        _mark_failure(current_job_id, "document_policy_changed", retryable=False)
        return True
    storage_path = settings.storage_root / storage_key
    try:
        parsed = parse_document(storage_path)
    except (DocumentLimitExceeded, UnsupportedDocument) as error:
        code = (
            "document_limit_exceeded"
            if isinstance(error, DocumentLimitExceeded)
            else "unsupported_document"
        )
        _mark_failure(current_job_id, code, retryable=False)
        return True
    except Exception:
        logger.exception("document extraction failed", extra={"job_id": str(current_job_id)})
        _mark_failure(current_job_id, "extraction_failed", retryable=False)
        return True

    chunks = chunk_blocks(parsed.blocks)
    vectors: list[list[float]] | None = None
    if settings.mode == "rag":
        if not embedding_model or not settings.embedding_model:
            _mark_failure(current_job_id, "embedding_model_not_configured", retryable=False)
            return True
        if embedding_model != settings.embedding_model:
            _mark_failure(current_job_id, "index_generation_model_mismatch", retryable=False)
            return True
        provider = OllamaProvider(
            settings.ollama_url,
            embedding_model=embedding_model,
            generation_model=settings.generation_model or "",
        )
        try:
            vectors = asyncio.run(provider.embed([chunk.content for chunk in chunks]))
        except ProviderUnavailable:
            _mark_failure(current_job_id, "embedding_provider_unavailable", retryable=True)
            return True
        dimensions = len(vectors[0]) if vectors else 0
        if expected_dimensions is not None and dimensions != expected_dimensions:
            _mark_failure(current_job_id, "embedding_dimension_mismatch", retryable=False)
            return True

    with SessionFactory.begin() as session:
        job = session.scalar(
            select(IngestionJob).where(IngestionJob.id == current_job_id).with_for_update()
        )
        document = session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        version = session.get(DocumentVersion, version_id)
        generation = session.scalar(
            select(IndexGeneration).where(IndexGeneration.id == generation_id).with_for_update()
        )
        if job is None or document is None or version is None or generation is None:
            return True
        if (
            document.deleted_at is not None
            or document.revoked_at is not None
            or document.security_epoch != job_epoch
            or job.security_epoch != job_epoch
            or job.state != "running"
        ):
            job.state = "failed"
            job.error_code = "document_policy_changed"
            job.lease_until = None
            return True
        active_version = (
            session.get(DocumentVersion, document.active_version_id)
            if document.active_version_id is not None
            else None
        )
        if active_version is not None and active_version.version_number > version.version_number:
            job.state = "failed"
            job.error_code = "superseded_version"
            job.lease_until = None
            return True

        index = DocumentIndex(version_id=version_id, generation_id=generation_id, state="staging")
        session.add(index)
        session.flush()
        existing_extraction = session.scalar(
            select(ExtractionBlock.id).where(ExtractionBlock.version_id == version_id).limit(1)
        )
        if existing_extraction is None:
            for sequence, block in enumerate(parsed.blocks):
                session.add(
                    ExtractionBlock(
                        version_id=version_id,
                        sequence_number=sequence,
                        content=block.text,
                        locator=block.locator,
                        extraction_method=block.extraction_method,
                    )
                )
        for position, chunk in enumerate(chunks):
            session.add(
                Chunk(
                    document_index_id=index.id,
                    version_id=version_id,
                    generation_id=generation_id,
                    sequence_number=chunk.sequence,
                    content=chunk.content,
                    content_sha256=chunk.content_sha256,
                    language=None,
                    locator=chunk.locator,
                    embedding=vectors[position] if vectors is not None else None,
                )
            )
        index.state = "ready"
        version.state = "ready"
        version.parser_version = "knowledgedesk-parsers-v1"
        document.active_version_id = version.id
        document.sha256 = parsed.sha256
        if vectors is not None:
            generation.embedding_dimensions = len(vectors[0]) if vectors else 0
        job.state = "ready"
        job.error_code = None
        job.lease_until = None
        job.updated_at = datetime.now(UTC)
        session.flush()
        incomplete_generation_jobs = session.scalar(
            select(IngestionJob.id)
            .where(
                IngestionJob.generation_id == generation.id,
                IngestionJob.state != "ready",
            )
            .limit(1)
        )
        if generation.state == "staging" and incomplete_generation_jobs is None:
            active_generations = session.scalars(
                select(IndexGeneration).where(
                    IndexGeneration.knowledge_base_id == generation.knowledge_base_id,
                    IndexGeneration.state == "active",
                )
            ).all()
            for active_generation in active_generations:
                active_generation.state = "retired"
            generation.state = "active"
        session.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_id=None,
                event_type="document.indexed",
                subject_id=document.id,
                details={
                    "version": version.version_number,
                    "generation": generation.generation_number,
                    "block_count": len(parsed.blocks),
                    "chunk_count": len(chunks),
                    "warnings": list(parsed.warnings),
                },
            )
        )
    return True


def recover_abandoned_jobs() -> int:
    now = datetime.now(UTC)
    with SessionFactory.begin() as session:
        abandoned = session.scalars(
            select(IngestionJob)
            .where(
                IngestionJob.state == "running",
                IngestionJob.lease_until < now,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in abandoned:
            job.lease_until = None
            job.updated_at = now
            if job.attempts >= MAX_ATTEMPTS:
                job.state = "failed"
                job.error_code = "worker_lease_expired"
                version = session.get(DocumentVersion, job.version_id)
                if version is not None and version.state != "ready":
                    version.state = "failed"
                    version.error_code = "worker_lease_expired"
            else:
                job.state = "queued"
        return len(abandoned)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    while True:
        if not process_one():
            time.sleep(2)


if __name__ == "__main__":
    main()

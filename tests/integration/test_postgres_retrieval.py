import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from knowledgedesk.persistence.models import (
    AccessGroup,
    Base,
    Chunk,
    Document,
    DocumentGroup,
    DocumentIndex,
    DocumentVersion,
    ExtractionBlock,
    GroupMembership,
    IndexGeneration,
    IngestionJob,
    KnowledgeBase,
    Membership,
    User,
    Workspace,
)
from knowledgedesk.retrieval.service import lexical_search, revalidate_hits, vector_search


@pytest.fixture
def pg_session() -> Iterator[Session]:
    url = os.environ.get("KD_TEST_DATABASE_URL")
    if not url:
        pytest.skip("KD_TEST_DATABASE_URL not provided; use scripts/test-integration.ps1")
    engine = create_engine(url, pool_pre_ping=True)
    if engine.url.database != "knowledgedesk_test":
        pytest.fail("integration tests are restricted to database `knowledgedesk_test`")
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_authorization_corpus(session: Session) -> tuple[User, Workspace, KnowledgeBase]:
    user = User(email="reader@example.invalid", password_hash="not-used", display_name="Reader")
    north = Workspace(name="North")
    south = Workspace(name="South")
    session.add_all([user, north, south])
    session.flush()
    session.add(Membership(workspace_id=north.id, user_id=user.id, role="reader"))
    sales = AccessGroup(workspace_id=north.id, name="Sales")
    finance = AccessGroup(workspace_id=north.id, name="Finance")
    north_base = KnowledgeBase(workspace_id=north.id, name="Manuals")
    south_base = KnowledgeBase(workspace_id=south.id, name="Manuals")
    session.add_all([sales, finance, north_base, south_base])
    session.flush()
    session.add(GroupMembership(group_id=sales.id, user_id=user.id))
    generation = IndexGeneration(
        knowledge_base_id=north_base.id,
        generation_number=1,
        embedding_model="unit-test-fixed-vectors-not-a-semantic-model",
        embedding_dimensions=3,
        state="active",
    )
    other_generation = IndexGeneration(
        knowledge_base_id=south_base.id,
        generation_number=1,
        state="active",
    )
    session.add_all([generation, other_generation])
    session.flush()

    def document(
        base: KnowledgeBase, content: str, *, public: bool
    ) -> tuple[Document, DocumentVersion, IndexGeneration, DocumentIndex]:
        record = Document(
            workspace_id=base.workspace_id,
            knowledge_base_id=base.id,
            title="synthetic document",
            format="md",
            storage_key=uuid4().hex,
            sha256=uuid4().hex + uuid4().hex,
            public_to_workspace=public,
        )
        session.add(record)
        session.flush()
        version = DocumentVersion(
            document_id=record.id,
            version_number=1,
            sha256=record.sha256,
            state="ready",
            valid_from=datetime.now(UTC),
        )
        session.add(version)
        session.flush()
        record.active_version_id = version.id
        index = DocumentIndex(
            version_id=version.id,
            generation_id=generation.id if base.id == north_base.id else other_generation.id,
            state="ready",
        )
        session.add(index)
        session.flush()
        return record, version, generation if base.id == north_base.id else other_generation, index

    allowed_doc, allowed_version, allowed_generation, allowed_index = document(
        north_base, "Warranty for AX-120 is 30 days. SAFE_NORTH_SALES", public=False
    )
    session.add(DocumentGroup(document_id=allowed_doc.id, group_id=sales.id))
    hidden_doc, hidden_version, hidden_generation, hidden_index = document(
        north_base, "Secret margin for AX-120 is 700. SECRET_NORTH_FINANCE", public=False
    )
    session.add(DocumentGroup(document_id=hidden_doc.id, group_id=finance.id))
    other_doc, other_version, other_index_generation, other_index = document(
        south_base, "Secret supplier for AX-120 is X. SECRET_SOUTH_WORKSPACE", public=True
    )
    for _record, version, gen, index, content, vector in [
        (
            allowed_doc,
            allowed_version,
            allowed_generation,
            allowed_index,
            "Warranty for AX-120 is 30 days. SAFE_NORTH_SALES",
            [1.0, 0.0, 0.0],
        ),
        (
            hidden_doc,
            hidden_version,
            hidden_generation,
            hidden_index,
            "Secret margin for AX-120 is 700. SECRET_NORTH_FINANCE",
            [0.99, 0.01, 0.0],
        ),
        (
            other_doc,
            other_version,
            other_index_generation,
            other_index,
            "Secret supplier for AX-120 is X. SECRET_SOUTH_WORKSPACE",
            [1.0, 0.0, 0.0],
        ),
    ]:
        session.add(
            Chunk(
                document_index_id=index.id,
                version_id=version.id,
                generation_id=gen.id,
                sequence_number=0,
                content=content,
                content_sha256=uuid4().hex + uuid4().hex,
                locator={"heading": "Synthetic"},
                embedding=vector,
            )
        )
    session.commit()
    return user, north, north_base


def test_lexical_query_excludes_restricted_groups_and_other_workspaces(pg_session: Session) -> None:
    user, workspace, base = _seed_authorization_corpus(pg_session)

    hits = lexical_search(
        pg_session,
        user_id=user.id,
        workspace_id=workspace.id,
        base_id=base.id,
        query="AX-120",
    )

    assert len(hits) == 1
    assert "SAFE_NORTH_SALES" in hits[0].chunk.content
    assert all(
        marker not in " ".join(hit.chunk.content for hit in hits)
        for marker in ("SECRET_NORTH_FINANCE", "SECRET_SOUTH_WORKSPACE")
    )


def test_vector_query_applies_scope_before_returning_candidates(pg_session: Session) -> None:
    user, workspace, base = _seed_authorization_corpus(pg_session)

    hits = vector_search(
        pg_session,
        user_id=user.id,
        workspace_id=workspace.id,
        base_id=base.id,
        query_vector=[1.0, 0.0, 0.0],
        model_name="unit-test-fixed-vectors-not-a-semantic-model",
    )

    assert len(hits) == 1
    assert "SAFE_NORTH_SALES" in hits[0].chunk.content
    assert "SECRET_NORTH_FINANCE" not in hits[0].chunk.content
    assert "SECRET_SOUTH_WORKSPACE" not in hits[0].chunk.content


def test_revalidation_discards_all_hits_after_group_access_is_removed(
    pg_session: Session,
) -> None:
    user, workspace, base = _seed_authorization_corpus(pg_session)
    hits = lexical_search(
        pg_session,
        user_id=user.id,
        workspace_id=workspace.id,
        base_id=base.id,
        query="AX-120",
    )
    membership = pg_session.scalar(
        select(GroupMembership).where(GroupMembership.user_id == user.id)
    )
    assert membership is not None
    pg_session.delete(membership)
    pg_session.commit()

    assert (
        revalidate_hits(
            pg_session,
            user_id=user.id,
            workspace_id=workspace.id,
            base_id=base.id,
            hits=hits,
        )
        == []
    )


def _create_markdown_job(
    pg_session: Session,
    storage_root: Path | None,
    *,
    security_epoch: int = 0,
) -> tuple[UUID, UUID, UUID]:
    from hashlib import sha256

    from knowledgedesk.persistence.models import DocumentVersion

    content = b"# Synthetic policy\n\nKeep the AX-120 receipt for 30 days.\n"
    workspace = Workspace(name="Worker workspace")
    pg_session.add(workspace)
    pg_session.flush()
    base = KnowledgeBase(workspace_id=workspace.id, name="Worker base")
    pg_session.add(base)
    pg_session.flush()
    generation = IndexGeneration(
        knowledge_base_id=base.id,
        generation_number=1,
        state="active",
    )
    pg_session.add(generation)
    pg_session.flush()
    storage_key = uuid4().hex + ".md"
    digest = sha256(content).hexdigest()
    document = Document(
        workspace_id=workspace.id,
        knowledge_base_id=base.id,
        title="policy.md",
        format="markdown",
        storage_key=storage_key,
        sha256=digest,
        public_to_workspace=True,
        security_epoch=security_epoch,
    )
    pg_session.add(document)
    pg_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        sha256=digest,
        state="queued",
    )
    pg_session.add(version)
    pg_session.flush()
    job = IngestionJob(
        document_id=document.id,
        version_id=version.id,
        generation_id=generation.id,
        state="queued",
        security_epoch=0,
    )
    pg_session.add(job)
    pg_session.commit()
    if storage_root is not None:
        storage_root.mkdir(parents=True, exist_ok=True)
        (storage_root / storage_key).write_bytes(content)
    return document.id, version.id, job.id


def test_worker_persists_extraction_and_activates_index_atomically(
    pg_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knowledgedesk.settings import Settings
    from knowledgedesk.workers import ingest

    document_id, version_id, job_id = _create_markdown_job(pg_session, tmp_path)
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(storage_root=tmp_path))
    monkeypatch.setattr(
        ingest,
        "SessionFactory",
        sessionmaker(bind=pg_session.get_bind(), expire_on_commit=False),
    )

    assert ingest.process_one(job_id) is True

    document = pg_session.get(Document, document_id)
    version = pg_session.get(DocumentVersion, version_id)
    job = pg_session.get(IngestionJob, job_id)
    assert document is not None and document.active_version_id == version_id
    assert version is not None and version.state == "ready"
    assert job is not None and job.state == "ready"
    assert pg_session.scalar(select(Chunk).where(Chunk.version_id == version_id)) is not None
    assert (
        pg_session.scalar(select(ExtractionBlock).where(ExtractionBlock.version_id == version_id))
        is not None
    )
    assert ingest.process_one(job_id) is False
    assert len(pg_session.scalars(select(Chunk).where(Chunk.version_id == version_id)).all()) == 1


def test_worker_rejects_job_after_access_epoch_changed(
    pg_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knowledgedesk.settings import Settings
    from knowledgedesk.workers import ingest

    document_id, version_id, job_id = _create_markdown_job(pg_session, tmp_path, security_epoch=1)
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(storage_root=tmp_path))
    monkeypatch.setattr(
        ingest,
        "SessionFactory",
        sessionmaker(bind=pg_session.get_bind(), expire_on_commit=False),
    )

    assert ingest.process_one(job_id) is True

    document = pg_session.get(Document, document_id)
    version = pg_session.get(DocumentVersion, version_id)
    job = pg_session.get(IngestionJob, job_id)
    assert document is not None and document.active_version_id is None
    assert version is not None and version.state == "failed"
    assert job is not None and job.error_code == "document_policy_changed"
    assert pg_session.scalar(select(Chunk).where(Chunk.version_id == version_id)) is None


def test_abandoned_job_lease_is_requeued(
    pg_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knowledgedesk.workers import ingest

    monkeypatch.setattr(
        ingest,
        "SessionFactory",
        sessionmaker(bind=pg_session.get_bind(), expire_on_commit=False),
    )

    _, _, job_id = _create_markdown_job(pg_session, None)
    job = pg_session.get(IngestionJob, job_id)
    assert job is not None
    job.state = "running"
    job.attempts = 1
    job.lease_until = datetime.now(UTC) - timedelta(minutes=1)
    pg_session.commit()

    assert ingest.recover_abandoned_jobs() == 1

    job = pg_session.get(IngestionJob, job_id)
    assert job is not None and job.state == "queued" and job.lease_until is None


def test_staging_generation_becomes_active_only_after_reindex_finishes(
    pg_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knowledgedesk.settings import Settings
    from knowledgedesk.workers import ingest

    document_id, version_id, first_job_id = _create_markdown_job(pg_session, tmp_path)
    factory = sessionmaker(bind=pg_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(storage_root=tmp_path))
    monkeypatch.setattr(ingest, "SessionFactory", factory)
    assert ingest.process_one(first_job_id) is True

    document = pg_session.get(Document, document_id)
    assert document is not None
    old_generation = pg_session.scalar(
        select(IndexGeneration).where(
            IndexGeneration.knowledge_base_id == document.knowledge_base_id
        )
    )
    assert old_generation is not None
    staging = IndexGeneration(
        knowledge_base_id=document.knowledge_base_id,
        generation_number=2,
        state="staging",
    )
    pg_session.add(staging)
    pg_session.flush()
    job = IngestionJob(
        document_id=document.id,
        version_id=version_id,
        generation_id=staging.id,
        job_type="reindex",
        state="queued",
        security_epoch=document.security_epoch,
    )
    pg_session.add(job)
    pg_session.commit()

    assert ingest.process_one(job.id) is True
    pg_session.expire_all()
    assert pg_session.get(IndexGeneration, old_generation.id).state == "retired"  # type: ignore[union-attr]
    assert pg_session.get(IndexGeneration, staging.id).state == "active"  # type: ignore[union-attr]
    assert (
        len(
            pg_session.scalars(
                select(DocumentIndex).where(DocumentIndex.version_id == version_id)
            ).all()
        )
        == 2
    )

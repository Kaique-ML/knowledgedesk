from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(120))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Membership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        CheckConstraint("role IN ('admin', 'editor', 'reader')", name="ck_membership_role"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))


class AccessGroup(Base):
    __tablename__ = "access_groups"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    group_id: Mapped[UUID] = mapped_column(ForeignKey("access_groups.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_base_state", "knowledge_base_id", "deleted_at", "revoked_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(240))
    format: Mapped[str] = mapped_column(String(12))
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    conflict_key: Mapped[str | None] = mapped_column(String(120))
    public_to_workspace: Mapped[bool] = mapped_column(Boolean, default=True)
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True, name="fk_documents_active_version"),
        nullable=True,
    )
    security_epoch: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_state: Mapped[str] = mapped_column(String(16), default="not_requested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class DocumentGroup(Base):
    __tablename__ = "document_groups"
    __table_args__ = (UniqueConstraint("document_id", "group_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    group_id: Mapped[UUID] = mapped_column(ForeignKey("access_groups.id", ondelete="CASCADE"))


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number"),
        CheckConstraint(
            "state IN ('uploaded', 'queued', 'extracting', 'indexing', "
            "'ready', 'failed', 'archived')",
            name="ck_document_version_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="uploaded")
    parser_version: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ExtractionBlock(Base):
    __tablename__ = "extraction_blocks"
    __table_args__ = (UniqueConstraint("version_id", "sequence_number"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    sequence_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB)
    extraction_method: Mapped[str] = mapped_column(String(32))


class IndexGeneration(Base):
    __tablename__ = "index_generations"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "generation_number"),
        CheckConstraint(
            "state IN ('staging', 'active', 'retired', 'failed')", name="ck_generation_state"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    generation_number: Mapped[int] = mapped_column(Integer)
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    chunker_version: Mapped[str] = mapped_column(String(80), default="paragraph-char-v1")
    state: Mapped[str] = mapped_column(String(16), default="staging")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class DocumentIndex(Base):
    __tablename__ = "document_indexes"
    __table_args__ = (UniqueConstraint("version_id", "generation_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    generation_id: Mapped[UUID] = mapped_column(
        ForeignKey("index_generations.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(16), default="staging")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_index_id", "sequence_number"),
        Index("ix_chunks_lexical", "search_vector", postgresql_using="gin"),
        Index("ix_chunks_version", "version_id", "generation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_index_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_indexes.id", ondelete="CASCADE")
    )
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    generation_id: Mapped[UUID] = mapped_column(
        ForeignKey("index_generations.id", ondelete="CASCADE")
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(12))
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB)
    embedding: Mapped[Any | None] = mapped_column(Vector(), nullable=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True)
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", "generation_id", "job_type"),
        CheckConstraint("state IN ('queued', 'running', 'ready', 'failed')", name="ck_job_state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    generation_id: Mapped[UUID] = mapped_column(
        ForeignKey("index_generations.id", ondelete="CASCADE")
    )
    job_type: Mapped[str] = mapped_column(String(24), default="ingest")
    state: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    security_epoch: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    csrf_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunks.id", ondelete="RESTRICT"))
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT")
    )
    evidence_id: Mapped[str] = mapped_column(String(24))


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("message_id", "user_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ReviewRequest(Base):
    __tablename__ = "review_requests"
    __table_args__ = (
        CheckConstraint("state IN ('open', 'in_review', 'resolved')", name="ck_review_state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    question: Mapped[str] = mapped_column(String(2000))
    reason: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(16), default="open")
    assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[str | None] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(80))
    subject_id: Mapped[UUID | None] = mapped_column(Uuid)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from knowledgedesk.auth.sessions import (
    create_session,
    lookup_session,
    membership_role,
    verify_csrf,
    verify_password,
)
from knowledgedesk.generation.contracts import GroundedAnswer
from knowledgedesk.generation.relevance import keep_most_relevant_blocks
from knowledgedesk.persistence.database import get_db
from knowledgedesk.persistence.models import (
    AccessGroup,
    AuditEvent,
    Chunk,
    Citation,
    Conversation,
    Document,
    DocumentGroup,
    DocumentVersion,
    Feedback,
    GroupMembership,
    IndexGeneration,
    IngestionJob,
    KnowledgeBase,
    LoginSession,
    Membership,
    Message,
    ReviewRequest,
    User,
    Workspace,
)
from knowledgedesk.providers.ollama import InvalidModelResponse, OllamaProvider, ProviderUnavailable
from knowledgedesk.retrieval.service import (
    SearchHit,
    build_model_context,
    hybrid_search,
    lexical_search,
    revalidate_hits,
    vector_search,
)
from knowledgedesk.settings import Settings, get_settings

logger = logging.getLogger("knowledgedesk")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "web" / "templates"))
_question_windows: dict[UUID, deque[float]] = defaultdict(deque)
_question_windows_lock = Lock()


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: LoginSession


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class SearchBody(BaseModel):
    workspace_id: UUID
    base_id: UUID
    query: str = Field(min_length=1, max_length=2000)
    method: str = Field(default="hybrid", pattern="^(lexical|vector|hybrid)$")
    top_k: int = Field(default=6, ge=1, le=20)


class ConversationBody(BaseModel):
    workspace_id: UUID
    base_id: UUID


class MessageBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class FeedbackBody(BaseModel):
    rating: str = Field(pattern="^(useful|incorrect|bad_source|missing_information)$")


class ReviewBody(BaseModel):
    confirm: bool
    reason: str = Field(min_length=1, max_length=80)


class ResolveReviewBody(BaseModel):
    state: str = Field(pattern="^(in_review|resolved)$")
    resolution: str | None = Field(default=None, max_length=4000)


class DocumentAccessBody(BaseModel):
    public_to_workspace: bool
    group_ids: list[UUID] = Field(default_factory=list, max_length=64)


class KnowledgeBaseBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ReindexBody(BaseModel):
    embedding_model: str | None = Field(default=None, max_length=160)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=65_535)


def _provider(settings: Settings, *, require_generation: bool = True) -> OllamaProvider:
    if not settings.embedding_model:
        raise ProviderUnavailable("an embedding model must be configured")
    if require_generation and not settings.generation_model:
        raise ProviderUnavailable("a generation model must be configured")
    return OllamaProvider(
        settings.ollama_url,
        embedding_model=settings.embedding_model,
        generation_model=settings.generation_model or "",
        generation_timeout_seconds=settings.generation_timeout_seconds,
        generation_context_tokens=settings.generation_context_tokens,
        generation_max_tokens=settings.generation_max_tokens,
    )


def _auth_context(request: Request, session: Session) -> AuthContext:
    found = lookup_session(session, request.cookies.get("kd_session"))
    if found is None:
        raise HTTPException(status_code=401, detail={"code": "authentication_required"})
    return AuthContext(*found)


def _check_csrf(request: Request, auth: AuthContext) -> None:
    if not verify_csrf(
        auth.session,
        request.cookies.get("kd_csrf"),
        request.headers.get("x-csrf-token"),
    ):
        raise HTTPException(status_code=403, detail={"code": "csrf_validation_failed"})


def _check_question_rate(auth: AuthContext, settings: Settings) -> None:
    now = time.monotonic()
    with _question_windows_lock:
        window = _question_windows[auth.session.id]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= settings.question_rate_limit_per_minute:
            raise HTTPException(status_code=429, detail={"code": "question_rate_limit_exceeded"})
        window.append(now)


def _require_workspace(session: Session, user_id: UUID, workspace_id: UUID) -> str:
    role = membership_role(session, user_id, workspace_id)
    if role is None:
        raise HTTPException(status_code=404, detail={"code": "workspace_unavailable"})
    return role


def _require_base(
    session: Session, user_id: UUID, workspace_id: UUID, base_id: UUID
) -> KnowledgeBase:
    _require_workspace(session, user_id, workspace_id)
    base = session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    if base is None:
        raise HTTPException(status_code=404, detail={"code": "knowledge_base_unavailable"})
    return base


def _require_managed_document(
    session: Session,
    *,
    user_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
) -> tuple[Document, str]:
    role = _require_workspace(session, user_id, workspace_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail={"code": "editor_role_required"})
    document = session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "document_unavailable"})
    return document, role


def _active_generation(session: Session, base_id: UUID, settings: Settings) -> IndexGeneration:
    generation = session.scalar(
        select(IndexGeneration).where(
            IndexGeneration.knowledge_base_id == base_id,
            IndexGeneration.state == "active",
        )
    )
    wanted_model = settings.embedding_model if settings.mode == "rag" else None
    wanted_dimensions = settings.embedding_dimensions if settings.mode == "rag" else None
    if generation is None:
        generation = IndexGeneration(
            knowledge_base_id=base_id,
            generation_number=1,
            embedding_model=wanted_model,
            embedding_dimensions=wanted_dimensions,
            state="active",
        )
        session.add(generation)
        session.flush()
    elif settings.mode == "rag" and (
        generation.embedding_model != wanted_model
        or (
            wanted_dimensions is not None
            and generation.embedding_dimensions not in {None, wanted_dimensions}
        )
    ):
        raise HTTPException(status_code=409, detail={"code": "reindex_required_for_model_change"})
    return generation


def _source(hit: SearchHit) -> dict[str, object]:
    return {
        "evidence_id": hit.evidence_id,
        "title": hit.document.title,
        "version": hit.version.version_number,
        "locator": hit.chunk.locator,
        "excerpt": hit.chunk.content,
        "score": hit.score,
        "source_url": f"/api/v1/sources/{hit.document.id}/{hit.version.id}/{hit.chunk.id}",
    }


def _retrieve(
    session: Session,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    base_id: UUID,
    query: str,
    method: str,
    top_k: int,
) -> list[SearchHit]:
    if method == "lexical":
        return lexical_search(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            base_id=base_id,
            query=query,
            limit=top_k,
        )
    generation = session.scalar(
        select(IndexGeneration).where(
            IndexGeneration.knowledge_base_id == base_id,
            IndexGeneration.state == "active",
        )
    )
    if generation is None or not generation.embedding_model:
        raise ProviderUnavailable("the active index has no embedding model")
    provider = OllamaProvider(
        settings.ollama_url,
        embedding_model=generation.embedding_model,
        generation_model=settings.generation_model or "",
    )
    import asyncio

    query_vector = asyncio.run(provider.embed([query], query=True))[0]
    vector_hits = vector_search(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        base_id=base_id,
        query_vector=query_vector,
        model_name=generation.embedding_model,
        limit=top_k,
    )
    if method == "vector":
        return vector_hits
    lexical_hits = lexical_search(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        base_id=base_id,
        query=query,
        limit=top_k,
    )
    return hybrid_search(lexical_hits, vector_hits, limit=top_k)


async def _retrieve_async(
    session: Session,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    base_id: UUID,
    query: str,
    method: str,
    top_k: int,
) -> list[SearchHit]:
    if method == "lexical":
        return lexical_search(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            base_id=base_id,
            query=query,
            limit=top_k,
        )
    generation = session.scalar(
        select(IndexGeneration).where(
            IndexGeneration.knowledge_base_id == base_id,
            IndexGeneration.state == "active",
        )
    )
    if generation is None or not generation.embedding_model:
        raise ProviderUnavailable("the active index has no embedding model")
    provider = OllamaProvider(
        settings.ollama_url,
        embedding_model=generation.embedding_model,
        generation_model=settings.generation_model or "",
    )
    query_vector = (await provider.embed([query], query=True))[0]
    vector_hits = vector_search(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        base_id=base_id,
        query_vector=query_vector,
        model_name=generation.embedding_model,
        limit=top_k,
    )
    if method == "vector":
        return vector_hits
    lexical_hits = lexical_search(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        base_id=base_id,
        query=query,
        limit=top_k,
    )
    return hybrid_search(lexical_hits, vector_hits, limit=top_k)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    yield


app = FastAPI(title="KnowledgeDesk", version="0.1.0", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent.parent / "web" / "static")),
    name="static",
)


@app.middleware("http")
async def correlation_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("x-request-id", "")
    if not request_id or len(request_id) > 80 or not request_id.isascii():
        request_id = secrets.token_hex(12)
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        json.dumps(
            {
                "event": "http.request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
    )
    return response


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def ready(session: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    settings = get_settings()
    if settings.mode == "rag":
        if not settings.embedding_model or not settings.generation_model:
            raise HTTPException(status_code=503, detail={"code": "rag_models_not_configured"})
        import httpx

        try:
            response = httpx.get(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=3)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise HTTPException(
                status_code=503, detail={"code": "generation_provider_unavailable"}
            ) from error
    return {"status": "ready", "mode": settings.mode}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, session: Annotated[Session, Depends(get_db)]) -> HTMLResponse:
    auth = None
    try:
        auth = _auth_context(request, session)
    except HTTPException:
        pass
    workspaces: Sequence[Workspace] = ()
    csrf_token = request.cookies.get("kd_csrf", "")
    if auth is not None:
        workspaces = session.scalars(
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == auth.user.id)
            .order_by(Workspace.name)
        ).all()
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "user": auth.user if auth else None,
            "mode": get_settings().mode,
            "workspaces": workspaces,
            "csrf_token": csrf_token,
        },
    )
    if auth is None:
        csrf_token = secrets.token_urlsafe(32)
        response.set_cookie(
            "kd_csrf",
            csrf_token,
            httponly=False,
            secure=get_settings().secure_cookies,
            samesite="lax",
        )
    return response


@app.post("/api/v1/login")
def login(
    body: LoginBody,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    user = session.scalar(select(User).where(User.email == body.email.strip().casefold()))
    if user is None or not verify_password(body.password, user.password_hash) or user.disabled_at:
        raise HTTPException(status_code=401, detail={"code": "invalid_credentials"})
    settings = get_settings()
    token, csrf, record = create_session(session, user, settings)
    session.commit()
    response.set_cookie(
        "kd_session",
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_seconds,
        path="/",
    )
    response.set_cookie(
        "kd_csrf",
        csrf,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_seconds,
        path="/",
    )
    return {"status": "authenticated", "user_id": str(user.id), "csrf_token": csrf}


@app.post("/api/v1/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
) -> Response:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    session.delete(auth.session)
    session.commit()
    response.delete_cookie("kd_session", path="/")
    response.delete_cookie("kd_csrf", path="/")
    response.status_code = 204
    return response


@app.get("/api/v1/me")
def me(request: Request, session: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    auth = _auth_context(request, session)
    workspaces = session.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == auth.user.id)
        .order_by(Workspace.name)
    ).all()
    return {
        "id": str(auth.user.id),
        "email": auth.user.email,
        "display_name": auth.user.display_name,
        "workspaces": [
            {"id": str(workspace.id), "name": workspace.name, "role": role}
            for workspace, role in workspaces
        ],
    }


@app.get("/api/v1/workspaces/{workspace_id}/bases")
def list_bases(
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, str]]:
    auth = _auth_context(request, session)
    _require_workspace(session, auth.user.id, workspace_id)
    bases = session.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.workspace_id == workspace_id)
        .order_by(KnowledgeBase.name)
    ).all()
    return [{"id": str(base.id), "name": base.name} for base in bases]


@app.post("/api/v1/workspaces/{workspace_id}/bases", status_code=201)
def create_base(
    workspace_id: UUID,
    body: KnowledgeBaseBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    role = _require_workspace(session, auth.user.id, workspace_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail={"code": "editor_role_required"})
    name = body.name.strip()
    existing = session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.workspace_id == workspace_id,
            func.lower(KnowledgeBase.name) == name.casefold(),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "knowledge_base_name_exists"})
    base = KnowledgeBase(workspace_id=workspace_id, name=name)
    session.add(base)
    session.flush()
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=auth.user.id,
            event_type="knowledge_base.created",
            subject_id=base.id,
            details={"name": name},
        )
    )
    session.commit()
    return {"id": str(base.id), "name": base.name}


@app.post("/api/v1/bases/{base_id}/reindex", status_code=202)
def reindex_base(
    base_id: UUID,
    workspace_id: UUID,
    body: ReindexBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    role = _require_workspace(session, auth.user.id, workspace_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail={"code": "editor_role_required"})
    base = _require_base(session, auth.user.id, workspace_id, base_id)
    if session.scalar(
        select(IndexGeneration.id).where(
            IndexGeneration.knowledge_base_id == base.id,
            IndexGeneration.state == "staging",
        )
    ):
        raise HTTPException(status_code=409, detail={"code": "reindex_already_running"})
    model = body.embedding_model or settings.embedding_model
    dimensions = body.embedding_dimensions or settings.embedding_dimensions
    if settings.mode == "rag" and (not model or not dimensions):
        raise HTTPException(status_code=422, detail={"code": "embedding_configuration_required"})
    number = (
        session.scalar(
            select(func.max(IndexGeneration.generation_number)).where(
                IndexGeneration.knowledge_base_id == base.id
            )
        )
        or 0
    ) + 1
    generation = IndexGeneration(
        knowledge_base_id=base.id,
        generation_number=number,
        embedding_model=model if settings.mode == "rag" else None,
        embedding_dimensions=dimensions if settings.mode == "rag" else None,
        state="staging",
    )
    session.add(generation)
    session.flush()
    documents = session.scalars(
        select(Document).where(
            Document.knowledge_base_id == base.id,
            Document.active_version_id.is_not(None),
            Document.deleted_at.is_(None),
            Document.revoked_at.is_(None),
        )
    ).all()
    if not documents:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "no_active_documents"})
    for document in documents:
        session.add(
            IngestionJob(
                document_id=document.id,
                version_id=document.active_version_id,
                generation_id=generation.id,
                job_type="reindex",
                state="queued",
                security_epoch=document.security_epoch,
            )
        )
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=auth.user.id,
            event_type="knowledge_base.reindex_queued",
            subject_id=base.id,
            details={"generation": number, "document_count": len(documents)},
        )
    )
    session.commit()
    return {
        "generation_id": str(generation.id),
        "generation": number,
        "state": "staging",
        "job_count": len(documents),
    }


@app.get("/api/v1/workspaces/{workspace_id}/groups")
def list_groups(
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, str]]:
    auth = _auth_context(request, session)
    _require_workspace(session, auth.user.id, workspace_id)
    groups = session.scalars(
        select(AccessGroup)
        .where(AccessGroup.workspace_id == workspace_id)
        .order_by(AccessGroup.name)
    ).all()
    return [{"id": str(group.id), "name": group.name} for group in groups]


@app.get("/api/v1/bases/{base_id}/documents")
def list_documents(
    base_id: UUID,
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    auth = _auth_context(request, session)
    _require_base(session, auth.user.id, workspace_id, base_id)
    member = (
        select(Membership.id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == auth.user.id,
        )
        .exists()
    )
    group_member = (
        select(GroupMembership.id)
        .join(AccessGroup, AccessGroup.id == GroupMembership.group_id)
        .join(DocumentGroup, DocumentGroup.group_id == AccessGroup.id)
        .where(
            DocumentGroup.document_id == Document.id,
            AccessGroup.workspace_id == workspace_id,
            GroupMembership.user_id == auth.user.id,
        )
        .exists()
    )
    documents = session.scalars(
        select(Document)
        .where(
            Document.knowledge_base_id == base_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
            Document.revoked_at.is_(None),
            member,
            (Document.public_to_workspace.is_(True) | group_member),
        )
        .order_by(Document.created_at.desc())
    ).all()
    result: list[dict[str, object]] = []
    for document in documents:
        version = session.get(DocumentVersion, document.active_version_id)
        pending = session.scalar(
            select(IngestionJob.state)
            .where(IngestionJob.document_id == document.id)
            .order_by(IngestionJob.created_at.desc())
            .limit(1)
        )
        result.append(
            {
                "id": str(document.id),
                "title": document.title,
                "format": document.format,
                "version": version.version_number if version else None,
                "state": version.state if version else pending or "uploaded",
                "visibility": "workspace" if document.public_to_workspace else "groups",
            }
        )
    return result


@app.post("/api/v1/bases/{base_id}/documents", status_code=202)
async def upload_document(
    base_id: UUID,
    workspace_id: Annotated[UUID, Form()],
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    upload: Annotated[UploadFile, File()],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    role = _require_workspace(session, auth.user.id, workspace_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail={"code": "editor_role_required"})
    base = _require_base(session, auth.user.id, workspace_id, base_id)
    data = await upload.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail={"code": "empty_upload"})
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail={"code": "upload_too_large"})
    from knowledgedesk.ingestion.parsers import ParseLimits, UnsupportedDocument, detect_format

    try:
        format_name = detect_format(
            data,
            upload.filename or "document",
            ParseLimits(
                max_bytes=settings.max_upload_bytes,
                max_archive_bytes=settings.max_archive_bytes,
                max_pixels=settings.max_image_pixels,
                max_pages=settings.max_pdf_pages,
            ),
        )
    except (UnsupportedDocument, ValueError) as error:
        raise HTTPException(
            status_code=400, detail={"code": "unsupported_or_invalid_file"}
        ) from error

    generation = _active_generation(session, base.id, settings)

    from knowledgedesk.persistence.models import DocumentVersion

    storage_key = secrets.token_hex(32) + (".md" if format_name == "markdown" else "")
    storage_path = settings.storage_root / storage_key
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with storage_path.open("xb") as original:
            original.write(data)
            original.flush()
        digest = hashlib.sha256(data).hexdigest()
        document = Document(
            workspace_id=workspace_id,
            knowledge_base_id=base.id,
            title=Path(upload.filename or "document").name[:240],
            format=format_name,
            storage_key=storage_key,
            sha256=digest,
            public_to_workspace=True,
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            sha256=digest,
            storage_key=storage_key,
            state="queued",
        )
        session.add(version)
        session.flush()
        job = IngestionJob(
            document_id=document.id,
            version_id=version.id,
            generation_id=generation.id,
            state="queued",
            security_epoch=document.security_epoch,
        )
        session.add(job)
        session.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_id=auth.user.id,
                event_type="document.uploaded",
                subject_id=document.id,
                details={"format": format_name, "version": 1},
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        storage_path.unlink(missing_ok=True)
        raise
    return {"document_id": str(document.id), "version_id": str(version.id), "job_id": str(job.id)}


@app.get("/api/v1/documents/{document_id}/versions")
def list_document_versions(
    document_id: UUID,
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    auth = _auth_context(request, session)
    document, _ = _require_managed_document(
        session,
        user_id=auth.user.id,
        workspace_id=workspace_id,
        document_id=document_id,
    )
    versions = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
    ).all()
    return [
        {
            "id": str(version.id),
            "version": version.version_number,
            "state": version.state,
            "active": version.id == document.active_version_id,
            "error": version.error_code,
            "valid_from": version.valid_from,
            "valid_until": version.valid_until,
        }
        for version in versions
    ]


@app.post("/api/v1/documents/{document_id}/versions", status_code=202)
async def replace_document(
    document_id: UUID,
    workspace_id: Annotated[UUID, Form()],
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    upload: Annotated[UploadFile, File()],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    document, _ = _require_managed_document(
        session,
        user_id=auth.user.id,
        workspace_id=workspace_id,
        document_id=document_id,
    )
    data = await upload.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail={"code": "empty_upload"})
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail={"code": "upload_too_large"})
    from knowledgedesk.ingestion.parsers import ParseLimits, UnsupportedDocument, detect_format

    try:
        format_name = detect_format(
            data,
            upload.filename or document.title,
            ParseLimits(
                max_bytes=settings.max_upload_bytes,
                max_archive_bytes=settings.max_archive_bytes,
                max_pixels=settings.max_image_pixels,
                max_pages=settings.max_pdf_pages,
            ),
        )
    except (UnsupportedDocument, ValueError) as error:
        raise HTTPException(
            status_code=400, detail={"code": "unsupported_or_invalid_file"}
        ) from error
    if format_name != document.format:
        raise HTTPException(status_code=409, detail={"code": "replacement_format_mismatch"})
    generation = _active_generation(session, document.knowledge_base_id, settings)
    version_number = (
        session.scalar(
            select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.document_id == document.id
            )
        )
        or 0
    ) + 1
    storage_key = secrets.token_hex(32) + (".md" if format_name == "markdown" else "")
    storage_path = settings.storage_root / storage_key
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with storage_path.open("xb") as original:
            original.write(data)
            original.flush()
        digest = hashlib.sha256(data).hexdigest()
        version = DocumentVersion(
            document_id=document.id,
            version_number=version_number,
            sha256=digest,
            storage_key=storage_key,
            state="queued",
        )
        session.add(version)
        session.flush()
        job = IngestionJob(
            document_id=document.id,
            version_id=version.id,
            generation_id=generation.id,
            state="queued",
            security_epoch=document.security_epoch,
        )
        session.add(job)
        session.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_id=auth.user.id,
                event_type="document.replacement_queued",
                subject_id=document.id,
                details={"version": version_number},
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        storage_path.unlink(missing_ok=True)
        raise
    return {"document_id": str(document.id), "version_id": str(version.id), "job_id": str(job.id)}


@app.patch("/api/v1/documents/{document_id}/access")
def update_document_access(
    document_id: UUID,
    workspace_id: UUID,
    body: DocumentAccessBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    document, _ = _require_managed_document(
        session,
        user_id=auth.user.id,
        workspace_id=workspace_id,
        document_id=document_id,
    )
    group_ids = set(body.group_ids)
    if not body.public_to_workspace and not group_ids:
        raise HTTPException(status_code=422, detail={"code": "restricted_document_requires_group"})
    valid_groups = set(
        session.scalars(
            select(AccessGroup.id).where(
                AccessGroup.workspace_id == workspace_id,
                AccessGroup.id.in_(group_ids),
            )
        ).all()
    )
    if valid_groups != group_ids:
        raise HTTPException(status_code=422, detail={"code": "invalid_access_group"})
    session.execute(sql_delete(DocumentGroup).where(DocumentGroup.document_id == document.id))
    session.add_all(
        [DocumentGroup(document_id=document.id, group_id=group_id) for group_id in valid_groups]
    )
    document.public_to_workspace = body.public_to_workspace
    document.security_epoch += 1
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=auth.user.id,
            event_type="document.access_changed",
            subject_id=document.id,
            details={
                "public_to_workspace": body.public_to_workspace,
                "group_count": len(valid_groups),
                "security_epoch": document.security_epoch,
            },
        )
    )
    session.commit()
    return {
        "id": str(document.id),
        "public_to_workspace": document.public_to_workspace,
        "group_ids": sorted(str(group_id) for group_id in valid_groups),
        "security_epoch": document.security_epoch,
    }


@app.delete("/api/v1/documents/{document_id}", status_code=202)
def delete_document(
    document_id: UUID,
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    document, _ = _require_managed_document(
        session,
        user_id=auth.user.id,
        workspace_id=workspace_id,
        document_id=document_id,
    )
    now = datetime.now(UTC)
    document.deleted_at = now
    document.revoked_at = now
    document.active_version_id = None
    document.purge_state = "pending"
    document.security_epoch += 1
    session.execute(
        update(IngestionJob)
        .where(
            IngestionJob.document_id == document.id,
            IngestionJob.state.in_(["queued", "running"]),
        )
        .values(state="failed", error_code="document_deleted", lease_until=None)
    )
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=auth.user.id,
            event_type="document.deleted",
            subject_id=document.id,
            details={"purge_state": "pending"},
        )
    )
    session.commit()
    return {"id": str(document.id), "state": "deleted", "purge_state": "pending"}


@app.get("/api/v1/documents/{document_id}/original")
def download_original(
    document_id: UUID,
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    auth = _auth_context(request, session)
    member = (
        select(Membership.id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == auth.user.id,
        )
        .exists()
    )
    group_member = (
        select(GroupMembership.id)
        .join(AccessGroup, AccessGroup.id == GroupMembership.group_id)
        .join(DocumentGroup, DocumentGroup.group_id == AccessGroup.id)
        .where(
            DocumentGroup.document_id == Document.id,
            AccessGroup.workspace_id == workspace_id,
            GroupMembership.user_id == auth.user.id,
        )
        .exists()
    )
    document = session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
            Document.revoked_at.is_(None),
            member,
            (Document.public_to_workspace.is_(True) | group_member),
        )
    )
    if document is None or document.active_version_id is None:
        raise HTTPException(status_code=404, detail={"code": "document_unavailable"})
    version = session.get(DocumentVersion, document.active_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail={"code": "document_unavailable"})
    path = settings.storage_root / (version.storage_key or document.storage_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "original_not_available"})
    return FileResponse(path, filename=document.title, media_type="application/octet-stream")


@app.get("/api/v1/jobs/{job_id}")
def get_job(
    job_id: UUID,
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    auth = _auth_context(request, session)
    role = _require_workspace(session, auth.user.id, workspace_id)
    statement = (
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .where(IngestionJob.id == job_id, Document.workspace_id == workspace_id)
    )
    if role == "reader":
        group_member = (
            select(GroupMembership.id)
            .join(AccessGroup, AccessGroup.id == GroupMembership.group_id)
            .join(DocumentGroup, DocumentGroup.group_id == AccessGroup.id)
            .where(
                DocumentGroup.document_id == Document.id,
                AccessGroup.workspace_id == workspace_id,
                GroupMembership.user_id == auth.user.id,
            )
            .exists()
        )
        statement = statement.where(Document.public_to_workspace.is_(True) | group_member)
    job = session.scalar(statement)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "job_unavailable"})
    return {
        "id": str(job.id),
        "state": job.state,
        "attempts": job.attempts,
        "error": job.error_code,
    }


@app.post("/api/v1/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: UUID,
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    role = _require_workspace(session, auth.user.id, workspace_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail={"code": "editor_role_required"})
    job = session.scalar(
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .where(
            IngestionJob.id == job_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
            Document.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "job_unavailable"})
    if job.state != "failed":
        raise HTTPException(status_code=409, detail={"code": "job_is_not_failed"})
    document = session.get(Document, job.document_id)
    if document is None or document.security_epoch != job.security_epoch:
        raise HTTPException(status_code=409, detail={"code": "document_policy_changed"})
    job.state = "queued"
    job.attempts = 0
    job.error_code = None
    job.lease_until = None
    job.updated_at = datetime.now(UTC)
    session.commit()
    return {"id": str(job.id), "state": "queued"}


@app.post("/api/v1/search")
def search(
    body: SearchBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    auth = _auth_context(request, session)
    _require_base(session, auth.user.id, body.workspace_id, body.base_id)
    try:
        hits = _retrieve(
            session,
            settings,
            user_id=auth.user.id,
            workspace_id=body.workspace_id,
            base_id=body.base_id,
            query=body.query,
            method=body.method,
            top_k=body.top_k,
        )
    except ProviderUnavailable as error:
        raise HTTPException(
            status_code=503, detail={"code": "retrieval_provider_unavailable"}
        ) from error
    return {"mode": "search_only", "method": body.method, "sources": [_source(hit) for hit in hits]}


@app.post("/api/v1/conversations", status_code=201)
def create_conversation(
    body: ConversationBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    _require_base(session, auth.user.id, body.workspace_id, body.base_id)
    conversation = Conversation(
        workspace_id=body.workspace_id,
        user_id=auth.user.id,
        knowledge_base_id=body.base_id,
    )
    session.add(conversation)
    session.commit()
    return {"id": str(conversation.id)}


@app.get("/api/v1/conversations")
def list_conversations(
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    auth = _auth_context(request, session)
    _require_workspace(session, auth.user.id, workspace_id)
    conversations = session.scalars(
        select(Conversation)
        .where(
            Conversation.user_id == auth.user.id,
            Conversation.workspace_id == workspace_id,
        )
        .order_by(Conversation.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": str(conversation.id),
            "base_id": str(conversation.knowledge_base_id),
            "created_at": conversation.created_at,
        }
        for conversation in conversations
    ]


@app.get("/api/v1/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    auth = _auth_context(request, session)
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == auth.user.id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_unavailable"})
    _require_base(
        session,
        auth.user.id,
        conversation.workspace_id,
        conversation.knowledge_base_id,
    )
    messages = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at, Message.id)
    ).all()
    output: list[dict[str, object]] = []
    for message in messages:
        unavailable = False
        if message.role == "assistant":
            citations = session.scalars(
                select(Citation).where(Citation.message_id == message.id)
            ).all()
            for citation in citations:
                group_member = (
                    select(GroupMembership.id)
                    .join(AccessGroup, AccessGroup.id == GroupMembership.group_id)
                    .join(DocumentGroup, DocumentGroup.group_id == AccessGroup.id)
                    .where(
                        DocumentGroup.document_id == Document.id,
                        AccessGroup.workspace_id == conversation.workspace_id,
                        GroupMembership.user_id == auth.user.id,
                    )
                    .exists()
                )
                accessible = session.scalar(
                    select(Chunk.id)
                    .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        Chunk.id == citation.chunk_id,
                        DocumentVersion.id == citation.version_id,
                        Document.active_version_id == DocumentVersion.id,
                        Document.deleted_at.is_(None),
                        Document.revoked_at.is_(None),
                        Document.workspace_id == conversation.workspace_id,
                        (Document.public_to_workspace.is_(True) | group_member),
                    )
                )
                if accessible is None:
                    unavailable = True
                    break
        output.append(
            {
                "id": str(message.id),
                "role": message.role,
                "content": None if unavailable else message.content,
                "status": "source_unavailable" if unavailable else message.status,
                "created_at": message.created_at,
            }
        )
    return output


@app.post("/api/v1/conversations/{conversation_id}/messages")
async def ask_question(
    conversation_id: UUID,
    body: MessageBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    _check_question_rate(auth, settings)
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == auth.user.id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_unavailable"})
    workspace_id = conversation.workspace_id
    base_id = conversation.knowledge_base_id
    _require_base(session, auth.user.id, workspace_id, base_id)
    question = body.question.strip()
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=question,
        status="received",
    )
    session.add(user_message)
    session.flush()
    try:
        hits = await _retrieve_async(
            session,
            settings,
            user_id=auth.user.id,
            workspace_id=workspace_id,
            base_id=base_id,
            query=question,
            method="hybrid" if settings.mode == "rag" else "lexical",
            top_k=settings.retrieval_top_k,
        )
        hits = hits[: settings.response_top_k]
    except ProviderUnavailable as error:
        session.rollback()
        raise HTTPException(
            status_code=503, detail={"code": "retrieval_provider_unavailable"}
        ) from error
    response_mode: str
    if not hits:
        answer: GroundedAnswer | None = GroundedAnswer(
            status="insufficient_evidence",
            missing_information=[
                "Não encontrei informação suficiente nos documentos disponíveis para responder."
            ],
        )
        response_mode = "search_only" if settings.mode == "search" else settings.mode
    elif settings.mode != "rag":
        answer = None
        response_mode = "demo_simulated" if settings.mode == "demo" else "search_only"
    else:
        provider = _provider(settings)
        prompt_evidence = build_model_context(hits, max_characters=settings.context_characters)
        try:
            generated = await provider.generate(question, prompt_evidence)
        except InvalidModelResponse as error:
            session.rollback()
            raise HTTPException(
                status_code=502, detail={"code": "generation_response_rejected"}
            ) from error
        except ProviderUnavailable as error:
            session.rollback()
            raise HTTPException(
                status_code=503, detail={"code": "generation_provider_unavailable"}
            ) from error
        checked_hits = revalidate_hits(
            session,
            user_id=auth.user.id,
            workspace_id=workspace_id,
            base_id=base_id,
            hits=hits,
        )
        if not checked_hits:
            session.rollback()
            raise HTTPException(status_code=409, detail={"code": "source_access_changed_retry"})
        generated.validate_evidence_ids({item["evidence_id"] for item in prompt_evidence})
        evidence_text = {
            hit.evidence_id: f"{hit.document.title} {hit.chunk.content}" for hit in hits
        }
        generated = keep_most_relevant_blocks(generated, question, evidence_text)
        answer = generated
        response_mode = "rag"

    assistant = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=(
            "\n\n".join(block.text for block in answer.answer_blocks)
            if answer is not None
            else "Busca sem geração; consulte as fontes vinculadas."
        ),
        status=answer.status if answer is not None else response_mode,
    )
    session.add(assistant)
    session.flush()
    citation_by_evidence = {hit.evidence_id: hit for hit in hits}
    if answer is not None:
        for block in answer.answer_blocks:
            for evidence_id in block.evidence_ids:
                hit = citation_by_evidence.get(evidence_id)
                if hit is None:
                    session.rollback()
                    raise HTTPException(
                        status_code=502, detail={"code": "invalid_evidence_reference"}
                    )
                session.add(
                    Citation(
                        message_id=assistant.id,
                        chunk_id=hit.chunk.id,
                        version_id=hit.version.id,
                        evidence_id=evidence_id,
                    )
                )
    session.commit()
    return {
        "mode": response_mode,
        "status": answer.status if answer is not None else response_mode,
        "answer_blocks": (
            [block.model_dump() for block in answer.answer_blocks] if answer is not None else []
        ),
        "missing_information": answer.missing_information if answer is not None else [],
        "clarification_question": answer.clarification_question if answer is not None else None,
        "sources": [_source(hit) for hit in hits],
        "message_id": str(assistant.id),
    }


@app.get("/api/v1/sources/{document_id}/{version_id}/{chunk_id}")
def open_source(
    document_id: UUID,
    version_id: UUID,
    chunk_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    auth = _auth_context(request, session)
    member_workspaces = select(Membership.workspace_id).where(Membership.user_id == auth.user.id)
    document = session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.workspace_id.in_(member_workspaces),
            Document.deleted_at.is_(None),
            Document.revoked_at.is_(None),
            Document.active_version_id == version_id,
            (
                Document.public_to_workspace.is_(True)
                | select(DocumentGroup.id)
                .join(AccessGroup, AccessGroup.id == DocumentGroup.group_id)
                .join(GroupMembership, GroupMembership.group_id == AccessGroup.id)
                .where(
                    DocumentGroup.document_id == Document.id,
                    GroupMembership.user_id == auth.user.id,
                    AccessGroup.workspace_id == Document.workspace_id,
                )
                .exists()
            ),
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "source_unavailable"})
    chunk = session.scalar(
        select(Chunk).where(
            Chunk.id == chunk_id,
            Chunk.version_id == version_id,
        )
    )
    version = session.get(DocumentVersion, version_id)
    if chunk is None or version is None or version.document_id != document.id:
        raise HTTPException(status_code=404, detail={"code": "source_unavailable"})
    return {
        "title": document.title,
        "version": version.version_number,
        "excerpt": chunk.content,
        "locator": chunk.locator,
        "format": document.format,
    }


@app.post("/api/v1/messages/{message_id}/feedback", status_code=201)
def submit_feedback(
    message_id: UUID,
    body: FeedbackBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    message = session.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.id == message_id, Conversation.user_id == auth.user.id)
    )
    if message is None:
        raise HTTPException(status_code=404, detail={"code": "message_unavailable"})
    conversation = session.get(Conversation, message.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail={"code": "message_unavailable"})
    _require_workspace(session, auth.user.id, conversation.workspace_id)
    feedback = Feedback(message_id=message.id, user_id=auth.user.id, rating=body.rating)
    session.add(feedback)
    session.commit()
    return {"status": "recorded"}


@app.post("/api/v1/conversations/{conversation_id}/review", status_code=201)
def request_review(
    conversation_id: UUID,
    body: ReviewBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    if not body.confirm:
        raise HTTPException(status_code=400, detail={"code": "review_confirmation_required"})
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == auth.user.id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_unavailable"})
    _require_base(
        session,
        auth.user.id,
        conversation.workspace_id,
        conversation.knowledge_base_id,
    )
    latest_question = session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if latest_question is None:
        raise HTTPException(status_code=400, detail={"code": "question_required"})
    review = ReviewRequest(
        workspace_id=conversation.workspace_id,
        user_id=auth.user.id,
        conversation_id=conversation.id,
        question=latest_question.content,
        reason=body.reason,
        state="open",
    )
    session.add(review)
    session.commit()
    return {"id": str(review.id), "state": review.state}


@app.get("/api/v1/reviews")
def list_reviews(
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    auth = _auth_context(request, session)
    role = _require_workspace(session, auth.user.id, workspace_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail={"code": "reviewer_role_required"})
    reviews = session.scalars(
        select(ReviewRequest)
        .where(ReviewRequest.workspace_id == workspace_id)
        .order_by(ReviewRequest.created_at.desc())
    ).all()
    return [
        {
            "id": str(review.id),
            "question": review.question,
            "reason": review.reason,
            "state": review.state,
        }
        for review in reviews
    ]


@app.patch("/api/v1/reviews/{review_id}")
def update_review(
    review_id: UUID,
    workspace_id: UUID,
    body: ResolveReviewBody,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    auth = _auth_context(request, session)
    _check_csrf(request, auth)
    role = _require_workspace(session, auth.user.id, workspace_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail={"code": "reviewer_role_required"})
    review = session.get(ReviewRequest, review_id)
    if review is None or review.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail={"code": "review_unavailable"})
    review.state = body.state
    review.assignee_id = auth.user.id
    review.resolution = body.resolution if body.state == "resolved" else None
    session.commit()
    return {"id": str(review.id), "state": review.state}


@app.get("/api/v1/evaluation/status")
def evaluation_status(
    request: Request, session: Annotated[Session, Depends(get_db)]
) -> dict[str, object]:
    auth = _auth_context(request, session)
    roles = session.scalars(select(Membership.role).where(Membership.user_id == auth.user.id)).all()
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail={"code": "admin_role_required"})
    dataset = Path("evals/datasets/questions-v1.jsonl")
    case_count = len(dataset.read_text(encoding="utf-8").splitlines()) if dataset.is_file() else 0
    results = sorted(Path("evals/results").glob("*.json"), reverse=True)
    latest: dict[str, object] | None = None
    if results:
        try:
            report = json.loads(results[0].read_text(encoding="utf-8"))
            latest = {"file": results[0].name, "manifest": report.get("manifest", {})}
        except OSError, ValueError, TypeError:
            latest = {"file": results[0].name, "error": "invalid_result_file"}
    return {
        "status": "COMPLETED" if latest else "NOT_RUN",
        "mode": get_settings().mode,
        "dataset": dataset.name if case_count else "not_available",
        "case_count": case_count,
        "latest": latest,
    }


@app.get("/api/v1/metrics")
def operational_metrics(
    request: Request, session: Annotated[Session, Depends(get_db)]
) -> dict[str, object]:
    auth = _auth_context(request, session)
    admin_workspaces = set(
        session.scalars(
            select(Membership.workspace_id).where(
                Membership.user_id == auth.user.id,
                Membership.role == "admin",
            )
        ).all()
    )
    if not admin_workspaces:
        raise HTTPException(status_code=403, detail={"code": "admin_role_required"})
    job_rows = session.execute(
        select(IngestionJob.state, func.count(IngestionJob.id))
        .join(Document, Document.id == IngestionJob.document_id)
        .where(Document.workspace_id.in_(admin_workspaces))
        .group_by(IngestionJob.state)
    ).all()
    review_rows = session.execute(
        select(ReviewRequest.state, func.count(ReviewRequest.id))
        .where(ReviewRequest.workspace_id.in_(admin_workspaces))
        .group_by(ReviewRequest.state)
    ).all()
    return {
        "ingestion_jobs": {state: count for state, count in job_rows},
        "review_requests": {state: count for state, count in review_rows},
        "active_documents": session.scalar(
            select(func.count(Document.id)).where(
                Document.workspace_id.in_(admin_workspaces),
                Document.active_version_id.is_not(None),
                Document.deleted_at.is_(None),
                Document.revoked_at.is_(None),
            )
        )
        or 0,
    }

"""Idempotently seed local synthetic demo identities, workspaces, groups and documents."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from sqlalchemy import select

from knowledgedesk.auth.sessions import hash_password
from knowledgedesk.ingestion.parsers import ParseLimits, detect_format
from knowledgedesk.persistence.database import SessionFactory
from knowledgedesk.persistence.models import (
    AccessGroup,
    Document,
    DocumentGroup,
    DocumentVersion,
    GroupMembership,
    IndexGeneration,
    IngestionJob,
    KnowledgeBase,
    Membership,
    User,
    Workspace,
)
from knowledgedesk.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    settings = get_settings()
    if settings.environment != "local":
        raise SystemExit("demo seed is disabled outside KD_ENVIRONMENT=local")
    password = os.environ.get("KD_DEMO_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("set KD_DEMO_PASSWORD to a local-only password of at least 12 characters")
    manifest_path = ROOT / "demo" / "corpus" / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("run scripts/generate_demo_assets.py first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    with SessionFactory.begin() as session:
        workspace = session.scalar(select(Workspace).where(Workspace.name == "Aurora Demo"))
        if workspace is None:
            workspace = Workspace(name="Aurora Demo")
            session.add(workspace)
            session.flush()
        other_workspace = session.scalar(select(Workspace).where(Workspace.name == "Boreal Demo"))
        if other_workspace is None:
            other_workspace = Workspace(name="Boreal Demo")
            session.add(other_workspace)
            session.flush()
        base = session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.workspace_id == workspace.id,
                KnowledgeBase.name == "Corporate Knowledge",
            )
        )
        if base is None:
            base = KnowledgeBase(workspace_id=workspace.id, name="Corporate Knowledge")
            session.add(base)
            session.flush()
        groups: dict[str, AccessGroup] = {}
        for name in ("sales", "finance"):
            group = session.scalar(
                select(AccessGroup).where(
                    AccessGroup.workspace_id == workspace.id, AccessGroup.name == name
                )
            )
            if group is None:
                group = AccessGroup(workspace_id=workspace.id, name=name)
                session.add(group)
                session.flush()
            groups[name] = group
        users = {
            "admin": ("admin@demo.invalid", "admin"),
            "sales": ("sales@demo.invalid", "reader"),
            "finance": ("finance@demo.invalid", "reader"),
            "reader": ("reader@demo.invalid", "reader"),
        }
        user_records: dict[str, User] = {}
        for key, (email, role) in users.items():
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    password_hash=hash_password(password),
                    display_name=f"Demo {key.title()}",
                )
                session.add(user)
                session.flush()
            user_records[key] = user
            if (
                session.scalar(
                    select(Membership).where(
                        Membership.workspace_id == workspace.id, Membership.user_id == user.id
                    )
                )
                is None
            ):
                session.add(Membership(workspace_id=workspace.id, user_id=user.id, role=role))
        for group_name, user_key in (
            ("sales", "sales"),
            ("finance", "finance"),
            ("sales", "admin"),
            ("finance", "admin"),
        ):
            if (
                session.scalar(
                    select(GroupMembership).where(
                        GroupMembership.group_id == groups[group_name].id,
                        GroupMembership.user_id == user_records[user_key].id,
                    )
                )
                is None
            ):
                session.add(
                    GroupMembership(
                        group_id=groups[group_name].id,
                        user_id=user_records[user_key].id,
                    )
                )
        generation = session.scalar(
            select(IndexGeneration).where(
                IndexGeneration.knowledge_base_id == base.id,
                IndexGeneration.state == "active",
            )
        )
        if generation is None:
            generation = IndexGeneration(
                knowledge_base_id=base.id,
                generation_number=1,
                embedding_model=settings.embedding_model if settings.mode == "rag" else None,
                embedding_dimensions=settings.embedding_dimensions
                if settings.mode == "rag"
                else None,
                state="active",
            )
            session.add(generation)
            session.flush()
        created = 0
        for item in manifest:
            source = ROOT / "demo" / "corpus" / str(item["path"])
            conflict_key = f"demo:{item['path']}"
            if session.scalar(select(Document).where(Document.conflict_key == conflict_key)):
                continue
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            format_name = detect_format(data, source.name, ParseLimits())
            storage_key = f"demo-{digest[:32]}{source.suffix.lower()}"
            destination = settings.storage_root / storage_key
            if not destination.exists():
                shutil.copyfile(source, destination)
            access = str(item["access"])
            document = Document(
                workspace_id=workspace.id,
                knowledge_base_id=base.id,
                title=str(item["title"]),
                format=format_name,
                storage_key=storage_key,
                sha256=digest,
                conflict_key=conflict_key,
                public_to_workspace=access == "public",
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
            if access in groups:
                session.add(DocumentGroup(document_id=document.id, group_id=groups[access].id))
            session.add(
                IngestionJob(
                    document_id=document.id,
                    version_id=version.id,
                    generation_id=generation.id,
                    state="queued",
                    security_epoch=document.security_epoch,
                )
            )
            created += 1
    print(f"demo seed ready; queued {created} new documents")


if __name__ == "__main__":
    main()

"""store immutable original key on each document version

Revision ID: b7c31fd8420a
Revises: a61390d4515e
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c31fd8420a"
down_revision: str | Sequence[str] | None = "a61390d4515e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("storage_key", sa.String(80), nullable=True))
    op.create_unique_constraint(
        "uq_document_versions_storage_key", "document_versions", ["storage_key"]
    )
    op.execute(
        "UPDATE document_versions AS version "
        "SET storage_key = document.storage_key "
        "FROM documents AS document "
        "WHERE version.document_id = document.id "
        "AND version.storage_key IS NULL "
        "AND version.id = document.active_version_id"
    )


def downgrade() -> None:
    op.drop_constraint("uq_document_versions_storage_key", "document_versions", type_="unique")
    op.drop_column("document_versions", "storage_key")

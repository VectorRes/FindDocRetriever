"""add version groups, version numbers and approval status (ID-HU-BE-015)

Existing documents are backfilled as "approved" (they were already being used
to answer questions) and grouped by filename. Their is_current /
superseded_by_id values are left as they were.

Revision ID: 0012_version_control
Revises: 0011_retrieval_audit
"""
import re
from pathlib import PurePath
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_version_control"
down_revision: Union[str, None] = "0011_retrieval_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copy of app.versioning.derive_version_group as of this migration, so
# later changes to the live grouping rule don't change what this backfill did.
_VERSION_SUFFIX = re.compile(
    r"(?:(?:^|[\s_.\-]+)(?:v|ver|version)[\s_.\-]?\d+(?:\.\d+)*|\s*\(\d+\))$",
    re.IGNORECASE,
)
_SEPARATORS = re.compile(r"[\s_.\-]+")


def _derive_version_group(filename: str, doc_type: str) -> str:
    stem = PurePath(filename).stem.strip().lower()
    previous = None
    while previous != stem:
        previous = stem
        stem = _VERSION_SUFFIX.sub("", stem).strip()
    normalized = _SEPARATORS.sub(" ", stem).strip() or PurePath(filename).stem.lower()
    return f"{doc_type}:{normalized}"


def upgrade() -> None:
    op.add_column("documents", sa.Column("version_group", sa.String(512), nullable=True))
    op.add_column(
        "documents", sa.Column("version_number", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "documents",
        sa.Column("approval_status", sa.String(16), nullable=False, server_default="approved"),
    )
    op.add_column(
        "documents", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, filename, doc_type FROM documents ORDER BY uploaded_at, id")
    ).fetchall()
    next_number: dict[str, int] = {}
    for doc_id, filename, doc_type in rows:
        group = _derive_version_group(filename, doc_type or "excel")
        next_number[group] = next_number.get(group, 0) + 1
        conn.execute(
            sa.text(
                "UPDATE documents SET version_group = :group, version_number = :number, "
                "approved_at = uploaded_at WHERE id = :id"
            ),
            {"group": group, "number": next_number[group], "id": doc_id},
        )

    op.alter_column("documents", "version_group", nullable=False)
    op.alter_column("documents", "version_number", server_default=None)
    op.alter_column("documents", "approval_status", server_default=None)
    op.create_index("ix_documents_version_group", "documents", ["version_group"])


def downgrade() -> None:
    op.drop_index("ix_documents_version_group", table_name="documents")
    op.drop_column("documents", "approved_at")
    op.drop_column("documents", "approval_status")
    op.drop_column("documents", "version_number")
    op.drop_column("documents", "version_group")

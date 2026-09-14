"""add minimal version/confidentiality fields to documents (ID-HU-FE-002)

Revision ID: 0008_doc_version_access
Revises: 0007_conversation_sessions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_doc_version_access"
down_revision: Union[str, None] = "0007_conversation_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "documents",
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_superseded_by_id",
        "documents",
        "documents",
        ["superseded_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "documents",
        sa.Column("confidentiality_tag", sa.String(32), nullable=False, server_default="public"),
    )
    op.alter_column("documents", "is_current", server_default=None)
    op.alter_column("documents", "confidentiality_tag", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_documents_superseded_by_id", "documents", type_="foreignkey")
    op.drop_column("documents", "confidentiality_tag")
    op.drop_column("documents", "superseded_by_id")
    op.drop_column("documents", "is_current")

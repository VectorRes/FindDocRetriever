"""access audit: denied attempts, insider flag and filterable context (ID-HU-BE-014)

Revision ID: 0013_access_audit
Revises: 0012_version_control
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "0013_access_audit"
down_revision: Union[str, None] = "0012_version_control"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "retrieval_audit_logs"


def upgrade() -> None:
    # Whole-document attempts (opening a restricted file) have no chunk.
    op.alter_column(TABLE, "chunk_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.alter_column(TABLE, "document_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column(TABLE, sa.Column("document_filename", sa.String(length=512), nullable=True))
    op.add_column(TABLE, sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(TABLE, sa.Column("resource", sa.String(length=32), nullable=False, server_default="retrieve"))
    op.add_column(TABLE, sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column(TABLE, sa.Column("is_insider", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(
        f"UPDATE {TABLE} AS log SET document_filename = documents.filename "
        f"FROM documents WHERE documents.id = log.document_id"
    )
    op.create_index("ix_retrieval_audit_logs_authorized_occurred_at", TABLE, ["authorized", "occurred_at"])
    op.create_index("ix_retrieval_audit_logs_document_filename", TABLE, ["document_filename"])
    op.create_index("ix_retrieval_audit_logs_request_id", TABLE, ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_retrieval_audit_logs_request_id", table_name=TABLE)
    op.drop_index("ix_retrieval_audit_logs_document_filename", table_name=TABLE)
    op.drop_index("ix_retrieval_audit_logs_authorized_occurred_at", table_name=TABLE)
    op.drop_column(TABLE, "is_insider")
    op.drop_column(TABLE, "request_id")
    op.drop_column(TABLE, "resource")
    op.drop_column(TABLE, "reason")
    op.drop_column(TABLE, "document_filename")
    op.execute(f"DELETE FROM {TABLE} WHERE chunk_id IS NULL OR document_id IS NULL")
    op.alter_column(TABLE, "document_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.alter_column(TABLE, "chunk_id", existing_type=UUID(as_uuid=True), nullable=False)

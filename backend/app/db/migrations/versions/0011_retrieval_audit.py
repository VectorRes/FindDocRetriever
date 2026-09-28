"""add retrieval audit logging for authorized and denied access events

Revision ID: 0011_retrieval_audit
Revises: 0010_confidentiality_access
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "0011_retrieval_audit"
down_revision: Union[str, None] = "0010_confidentiality_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "retrieval_audit_logs",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", UUID(as_uuid=True), nullable=False),
        sa.Column("confidentiality_tag", sa.String(length=64), nullable=False),
        sa.Column("authorized", sa.Boolean(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_audit_logs_occurred_at", "retrieval_audit_logs", ["occurred_at"])
    op.create_index("ix_retrieval_audit_logs_user_id", "retrieval_audit_logs", ["user_id"])
    op.create_index("ix_retrieval_audit_logs_document_id", "retrieval_audit_logs", ["document_id"])

def downgrade() -> None:
    op.drop_index("ix_retrieval_audit_logs_document_id", table_name="retrieval_audit_logs")
    op.drop_index("ix_retrieval_audit_logs_user_id", table_name="retrieval_audit_logs")
    op.drop_index("ix_retrieval_audit_logs_occurred_at", table_name="retrieval_audit_logs")
    op.drop_table("retrieval_audit_logs")

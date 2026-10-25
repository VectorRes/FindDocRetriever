"""sync failure handling: retry policy and failure log (ID-HU-INT-003)

Revision ID: 0020_sync_retry
Revises: 0019_permission_propagation
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "0020_sync_retry"
down_revision: Union[str, None] = "0019_permission_propagation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("synced_files", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("synced_files", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    # Files failing before this: one attempt made, retried on the next sync.
    op.execute("UPDATE synced_files SET attempts = 1 WHERE status = 'failed'")
    op.create_table(
        "sync_failures",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("remote_id", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sync_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_failures_source_id", "sync_failures", ["source_id"])
    op.create_index("ix_sync_failures_occurred_at", "sync_failures", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_failures_occurred_at", table_name="sync_failures")
    op.drop_index("ix_sync_failures_source_id", table_name="sync_failures")
    op.drop_table("sync_failures")
    op.drop_column("synced_files", "next_retry_at")
    op.drop_column("synced_files", "attempts")

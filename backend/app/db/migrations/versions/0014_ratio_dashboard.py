"""ratio dashboard: stored ratio calculations per entity and period (ID-HU-FE-004)

Revision ID: 0014_ratio_dashboard
Revises: 0013_access_audit
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "0014_ratio_dashboard"
down_revision: Union[str, None] = "0013_access_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dashboard_runs",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("entity", sa.String(length=255), nullable=False),
        sa.Column("entity_key", sa.String(length=255), nullable=False),
        sa.Column("period", sa.String(length=64), nullable=False),
        sa.Column("period_sort", sa.String(length=16), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("computed_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_key", "period", name="uq_dashboard_run_entity_period"),
    )
    op.create_index("ix_dashboard_runs_entity_key", "dashboard_runs", ["entity_key"])
    op.create_table(
        "ratio_snapshots",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("ratio_key", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("cross_document", sa.Boolean(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("missing_statements", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["dashboard_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ratio_snapshots_run_id", "ratio_snapshots", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_ratio_snapshots_run_id", table_name="ratio_snapshots")
    op.drop_table("ratio_snapshots")
    op.drop_index("ix_dashboard_runs_entity_key", table_name="dashboard_runs")
    op.drop_table("dashboard_runs")

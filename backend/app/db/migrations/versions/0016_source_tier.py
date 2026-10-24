"""documents.source_tier: source-of-truth hierarchy (ID-HU-BE-016)

Existing documents get the tier their file name suggests (app/source_tiers.py):
ERP/EPM exports, official reports, else internal reports.

Revision ID: 0016_source_tier
Revises: 0015_ratio_missing_inputs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_source_tier"
down_revision: Union[str, None] = "0015_ratio_missing_inputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.source_tiers import infer_tier

    op.add_column(
        "documents",
        sa.Column("source_tier", sa.String(length=32), nullable=False, server_default="internal_report"),
    )
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id, filename FROM documents")).all():
        tier = infer_tier(row.filename)
        if tier != "internal_report":
            bind.execute(sa.text("UPDATE documents SET source_tier = :t WHERE id = :id"), {"t": tier, "id": row.id})
    op.alter_column("documents", "source_tier", server_default=None)


def downgrade() -> None:
    op.drop_column("documents", "source_tier")

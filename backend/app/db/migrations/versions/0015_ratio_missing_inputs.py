"""ratio snapshots: "not_calculable" status and the missing inputs (ID-HU-BE-011)

Stored "unavailable" ratios become "not_calculable" and get the names of the
inputs that were missing: the inputs the engine didn't find, or, for a period
with no document for a statement, that statement's inputs.

Revision ID: 0015_ratio_missing_inputs
Revises: 0014_ratio_dashboard
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_ratio_missing_inputs"
down_revision: Union[str, None] = "0014_ratio_dashboard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load(value):
    return json.loads(value) if isinstance(value, str) else (value or [])


def upgrade() -> None:
    from app.ratios.formulas import LINE_ITEMS, RATIOS

    op.add_column(
        "ratio_snapshots",
        sa.Column("missing_inputs", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, ratio_key, inputs, missing_statements FROM ratio_snapshots WHERE status = 'unavailable'"
    )).all()
    for row in rows:
        missing = [i["name"] for i in _load(row.inputs) if not i.get("found")]
        definition = RATIOS.get(row.ratio_key)
        statements = _load(row.missing_statements)
        if not missing and definition is not None and statements:
            missing = [LINE_ITEMS[k].name for k in definition.inputs if LINE_ITEMS[k].statement in statements]
        bind.execute(
            sa.text("UPDATE ratio_snapshots SET status = 'not_calculable', missing_inputs = CAST(:m AS json) "
                    "WHERE id = :id"),
            {"m": json.dumps(missing), "id": row.id},
        )


def downgrade() -> None:
    op.execute("UPDATE ratio_snapshots SET status = 'unavailable' WHERE status = 'not_calculable'")
    op.drop_column("ratio_snapshots", "missing_inputs")

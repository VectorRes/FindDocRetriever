"""drop oversized ivfflat index on chunks.embedding

With lists=100 and only ~1k rows, ivfflat.probes defaulting to 1 means most
queries only scan one near-empty list and can silently return 0 rows for a
freshly-computed (not already-stored) query vector, even though the table
has plenty of relevant chunks. Rule of thumb for ivfflat is lists ~= rows/1000,
so at this scale an approximate index isn't warranted at all: an exact
sequential scan over a few thousand rows is effectively instant.

Once the table grows into the tens of thousands of rows, re-add an ivfflat
(or hnsw) index sized for that volume, e.g.:
    CREATE INDEX ix_chunks_embedding ON chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = <rows/1000>)
and remember to run ANALYZE chunks; after backfilling, and consider raising
ivfflat.probes (default 1) so more lists get scanned per query.

Revision ID: 0002_drop_ivfflat_idx
Revises: 0001_initial
Create Date: 2026-08-30

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_drop_ivfflat_idx"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

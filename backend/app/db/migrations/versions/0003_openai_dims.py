"""switch embedding column to OpenAI dimensions (1536)

Moving EMBEDDING_PROVIDER from local (bge-m3, 1024 dims) to openai
(text-embedding-3-small, 1536 dims). Existing embeddings are not compatible
across providers/dimensions, so ingested documents must be re-ingested after
this migration runs. TRUNCATE CASCADE clears documents and everything that
hangs off them (sheets, cells, named_ranges, chunks) so there's no orphaned
or dimension-mismatched data left behind.

Revision ID: 0003_openai_embedding_dims
Revises: 0002_drop_ivfflat_idx
Create Date: 2026-08-30

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_openai_dims"
down_revision: Union[str, None] = "0002_drop_ivfflat_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Wipes all previously ingested documents/sheets/cells/named_ranges/chunks —
    # they were embedded with the old provider and dimension, so they must be
    # re-ingested via POST /ingest after this migration completes.
    op.execute("TRUNCATE TABLE documents CASCADE")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1536)")


def downgrade() -> None:
    op.execute("TRUNCATE TABLE documents CASCADE")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1024)")

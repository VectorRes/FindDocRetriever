"""tag chunks with extraction method and OCR confidence

Revision ID: 0006_chunk_ocr_confidence
Revises: 0005_pdf_structured_data
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_chunk_ocr_confidence"
down_revision: Union[str, None] = "0005_pdf_structured_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("extraction_method", sa.String(16), nullable=False, server_default="native"))
    op.add_column("chunks", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("chunks", sa.Column("confidence_label", sa.String(64), nullable=True))
    op.alter_column("chunks", "extraction_method", server_default=None)
    op.create_index("ix_chunks_extraction_method", "chunks", ["extraction_method"])


def downgrade() -> None:
    op.drop_index("ix_chunks_extraction_method", table_name="chunks")
    op.drop_column("chunks", "confidence_label")
    op.drop_column("chunks", "confidence")
    op.drop_column("chunks", "extraction_method")

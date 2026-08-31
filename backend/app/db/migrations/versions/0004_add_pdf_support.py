"""add PDF support: documents.doc_type, chunks.page_number

Existing rows are all Excel workbooks, so doc_type backfills to 'excel'.
page_number is nullable and only populated for chunks that came from a PDF
(Excel chunks keep using sheet_name/cell_range instead).

Revision ID: 0004_add_pdf_support
Revises: 0003_openai_dims
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_pdf_support"
down_revision: Union[str, None] = "0003_openai_dims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("doc_type", sa.String(16), nullable=False, server_default="excel"),
    )
    op.alter_column("documents", "doc_type", server_default=None)

    op.add_column("chunks", sa.Column("page_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunks", "page_number")
    op.drop_column("documents", "doc_type")

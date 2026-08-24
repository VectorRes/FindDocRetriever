"""initial schema: documents, sheets, cells, named_ranges, chunks

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "sheets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_empty", sa.Boolean(), nullable=False),
        sa.Column("is_protected", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("document_id", "name", name="uq_sheet_document_name"),
    )

    op.create_table(
        "cells",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sheet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address", sa.String(16), nullable=False),
        sa.Column("row", sa.Integer(), nullable=False),
        sa.Column("column", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("formula_references", sa.JSON(), nullable=False),
        sa.Column("is_merged", sa.Boolean(), nullable=False),
        sa.Column("merged_range", sa.String(32), nullable=True),
        sa.UniqueConstraint("sheet_id", "address", name="uq_cell_sheet_address"),
    )
    op.create_index("ix_cells_sheet_id", "cells", ["sheet_id"])

    op.create_table(
        "named_ranges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sheet_name", sa.String(255), nullable=True),
        sa.Column("cell_range", sa.String(128), nullable=False),
    )

    embedding_dimensions = get_settings().embedding_dimensions
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sheet_name", sa.String(255), nullable=True),
        sa.Column("cell_range", sa.String(64), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(embedding_dimensions), nullable=False),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    # IVFFlat index for approximate nearest-neighbor search (cosine distance).
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("named_ranges")
    op.drop_table("cells")
    op.drop_table("sheets")
    op.drop_table("documents")
    op.execute("DROP EXTENSION IF EXISTS vector")

"""index structured PDF tables and note/section references

Revision ID: 0005_pdf_structured_data
Revises: 0004_add_pdf_support
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_pdf_structured_data"
down_revision: Union[str, None] = "0004_add_pdf_support"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "pdf_tables",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("table_index", sa.Integer(), nullable=False),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_label", sa.String(64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.UniqueConstraint("document_id", "page_number", "table_index", name="uq_pdf_table_page_index"),
    )
    op.create_index("ix_pdf_tables_document_id", "pdf_tables", ["document_id"])
    op.create_index("ix_pdf_tables_page_number", "pdf_tables", ["page_number"])

    op.create_table(
        "pdf_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(16), nullable=False),
        sa.Column("reference_number", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("document_id", "reference_type", "reference_number", "page_number", name="uq_pdf_reference"),
    )
    op.create_index("ix_pdf_references_document_id", "pdf_references", ["document_id"])
    op.create_index("ix_pdf_references_lookup", "pdf_references", ["document_id", "reference_type", "reference_number"])

    op.add_column("chunks", sa.Column("content_type", sa.String(32), nullable=False, server_default="text"))
    op.add_column("chunks", sa.Column("pdf_table_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("chunks", sa.Column("pdf_reference_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_chunks_pdf_table", "chunks", "pdf_tables", ["pdf_table_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_chunks_pdf_reference", "chunks", "pdf_references", ["pdf_reference_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_chunks_content_type", "chunks", ["content_type"])
    op.alter_column("chunks", "content_type", server_default=None)

def downgrade() -> None:
    op.drop_index("ix_chunks_content_type", table_name="chunks")
    op.drop_constraint("fk_chunks_pdf_reference", "chunks", type_="foreignkey")
    op.drop_constraint("fk_chunks_pdf_table", "chunks", type_="foreignkey")
    op.drop_column("chunks", "pdf_reference_id")
    op.drop_column("chunks", "pdf_table_id")
    op.drop_column("chunks", "content_type")
    op.drop_index("ix_pdf_references_lookup", table_name="pdf_references")
    op.drop_index("ix_pdf_references_document_id", table_name="pdf_references")
    op.drop_table("pdf_references")
    op.drop_index("ix_pdf_tables_page_number", table_name="pdf_tables")
    op.drop_index("ix_pdf_tables_document_id", table_name="pdf_tables")
    op.drop_table("pdf_tables")

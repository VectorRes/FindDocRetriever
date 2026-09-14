"""add storage_path to documents for original-file retrieval (ID-HU-FE-002)

Revision ID: 0009_document_storage_path
Revises: 0008_doc_version_access
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_document_storage_path"
down_revision: Union[str, None] = "0008_doc_version_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("storage_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "storage_path")

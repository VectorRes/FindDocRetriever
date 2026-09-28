"""enforce restrictive confidentiality defaults and add chunk-level tags

Revision ID: 0010_confidentiality_access
Revises: 0009_document_storage_path
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0010_confidentiality_access"
down_revision: Union[str, None] = "0009_document_storage_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.alter_column("documents", "confidentiality_tag",
                    existing_type=sa.String(length=32),
                    type_=sa.String(length=64),
                    server_default="restricted")
    op.add_column("chunks", sa.Column("confidentiality_tag", sa.String(64),
                                      nullable=False, server_default="restricted"))
    op.execute("""
        UPDATE chunks AS c
        SET confidentiality_tag = d.confidentiality_tag
        FROM documents AS d
        WHERE c.document_id = d.id
    """)
    op.alter_column("chunks", "confidentiality_tag", server_default=None)
    op.alter_column("documents", "confidentiality_tag", server_default=None)

def downgrade() -> None:
    op.drop_column("chunks", "confidentiality_tag")
    op.alter_column("documents", "confidentiality_tag",
                    existing_type=sa.String(length=64),
                    type_=sa.String(length=32),
                    server_default=None)

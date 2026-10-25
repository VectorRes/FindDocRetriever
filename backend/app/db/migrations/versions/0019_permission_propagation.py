"""permission propagation from synced repositories (ID-HU-INT-002)

Access rules per connected folder, and the permissions last read for each
synced file. Documents removed from a repository get status "removed"
(no column needed).

Revision ID: 0019_permission_propagation
Revises: 0018_repository_accounts
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_permission_propagation"
down_revision: Union[str, None] = "0018_repository_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sync_sources", sa.Column("permission_rules", sa.JSON(), nullable=False,
                                            server_default=sa.text("'[]'::json")))
    op.add_column("synced_files", sa.Column("permissions", sa.JSON(), nullable=False,
                                            server_default=sa.text("'[]'::json")))
    op.add_column("synced_files", sa.Column("access_tag", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("synced_files", "access_tag")
    op.drop_column("synced_files", "permissions")
    op.drop_column("sync_sources", "permission_rules")

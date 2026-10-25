"""repository accounts: Google / Microsoft sign-in (OAuth) for repository sync (ID-HU-INT-001)

Revision ID: 0018_repository_accounts
Revises: 0017_repository_sync
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "0018_repository_accounts"
down_revision: Union[str, None] = "0017_repository_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repository_accounts",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("connected_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "email", name="uq_repository_account_provider_email"),
    )
    op.add_column("sync_sources", sa.Column("account_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sync_sources_account", "sync_sources", "repository_accounts", ["account_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_sync_sources_account", "sync_sources", type_="foreignkey")
    op.drop_column("sync_sources", "account_id")
    op.drop_table("repository_accounts")

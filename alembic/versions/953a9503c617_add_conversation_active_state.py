"""add conversation active state

Revision ID: 953a9503c617
Revises: 8e69dcf53096
Create Date: 2026-08-01 22:35:14.118058
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "953a9503c617"
down_revision: str | None = "8e69dcf53096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discord_conversations",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index(
        op.f("ix_discord_conversations_is_active"),
        "discord_conversations",
        ["is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_discord_conversations_is_active"), table_name="discord_conversations")
    op.drop_column("discord_conversations", "is_active")

"""add utterance voice assets

Revision ID: c7f2a19d4e61
Revises: 953a9503c617
Create Date: 2026-08-24 13:55:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7f2a19d4e61"
down_revision: str | None = "953a9503c617"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "utterance_voice_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("utterance_id", sa.Uuid(), nullable=False),
        sa.Column("audio_relative_path", sa.String(length=500), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("audio_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("file_extension", sa.String(length=20), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["utterance_id"], ["utterances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audio_relative_path"),
        sa.UniqueConstraint("utterance_id"),
    )
    op.create_index(
        op.f("ix_utterance_voice_assets_utterance_id"),
        "utterance_voice_assets",
        ["utterance_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_utterance_voice_assets_utterance_id"),
        table_name="utterance_voice_assets",
    )
    op.drop_table("utterance_voice_assets")

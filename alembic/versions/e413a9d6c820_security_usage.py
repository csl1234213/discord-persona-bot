"""Persistent security budgets; no chat content."""

import sqlalchemy as sa

from alembic import op

revision = "e413a9d6c820"
down_revision = "c7f2a19d4e61"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_interactions",
        sa.Column("event_id", sa.String(24), primary_key=True),
        sa.Column("expires", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "security_usage",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("actor", sa.String(64), primary_key=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("admin_interactions")
    op.drop_table("security_usage")

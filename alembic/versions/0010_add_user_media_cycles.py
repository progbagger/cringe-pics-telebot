"""Add persistent per-user media delivery cycles.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_media_cycle_states",
        sa.Column("user_id", sa.BIGINT(), nullable=False),
        sa.Column("subscription_type_id", sa.BIGINT(), nullable=False),
        sa.Column("last_media_id", sa.BIGINT(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["last_media_id"], ["category_media.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subscription_type_id"], ["subscription_types.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "subscription_type_id"),
    )
    op.create_table(
        "user_media_cycle_entries",
        sa.Column("user_id", sa.BIGINT(), nullable=False),
        sa.Column("subscription_type_id", sa.BIGINT(), nullable=False),
        sa.Column("media_id", sa.BIGINT(), nullable=False),
        sa.Column("reservation_token", sa.Text(), nullable=True),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(reservation_token IS NOT NULL AND reserved_until IS NOT NULL AND shown_at IS NULL) OR "
            "(reservation_token IS NULL AND reserved_until IS NULL AND shown_at IS NOT NULL)",
            name="user_media_cycle_entries_state_consistent",
        ),
        sa.ForeignKeyConstraint(["media_id"], ["category_media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "subscription_type_id"],
            ["user_media_cycle_states.user_id", "user_media_cycle_states.subscription_type_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "subscription_type_id", "media_id"),
    )
    op.create_index(
        "user_media_cycle_entries_reservation_token_key",
        "user_media_cycle_entries",
        ["reservation_token"],
        unique=True,
        postgresql_where=sa.text("reservation_token IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "user_media_cycle_entries_reservation_token_key",
        table_name="user_media_cycle_entries",
    )
    op.drop_table("user_media_cycle_entries")
    op.drop_table("user_media_cycle_states")

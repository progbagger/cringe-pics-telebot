"""Add administrators and one-off broadcast history.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )

    op.create_table(
        "administrators",
        sa.Column("user_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "admin_broadcasts",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("created_by_user_id", sa.BIGINT(), nullable=False),
        sa.Column("source_chat_id", sa.BIGINT(), nullable=False),
        sa.Column("source_message_id", sa.BIGINT(), nullable=False),
        sa.Column("scheduled_local_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("timezone_offset_minutes", sa.SMALLINT(), nullable=True),
        sa.Column("status", sa.VARCHAR(length=16), server_default="scheduled", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "timezone_offset_minutes IS NULL OR timezone_offset_minutes BETWEEN -720 AND 840",
            name="admin_broadcasts_timezone_offset_minutes_range",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'sending', 'completed', 'deleted')",
            name="admin_broadcasts_status_values",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "admin_broadcasts_active_schedule_idx",
        "admin_broadcasts",
        ["status", "scheduled_local_at"],
        unique=False,
    )

    op.create_table(
        "admin_broadcast_deliveries",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("broadcast_id", sa.BIGINT(), nullable=False),
        sa.Column("user_id", sa.BIGINT(), nullable=False),
        sa.Column("status", sa.VARCHAR(length=16), server_default="pending", nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.VARCHAR(length=1000), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name="admin_broadcast_deliveries_status_values",
        ),
        sa.ForeignKeyConstraint(["broadcast_id"], ["admin_broadcasts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broadcast_id", "user_id", name="admin_broadcast_deliveries_broadcast_user_key"),
    )
    op.create_index(
        "admin_broadcast_deliveries_broadcast_status_idx",
        "admin_broadcast_deliveries",
        ["broadcast_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("admin_broadcast_deliveries_broadcast_status_idx", table_name="admin_broadcast_deliveries")
    op.drop_table("admin_broadcast_deliveries")
    op.drop_index("admin_broadcasts_active_schedule_idx", table_name="admin_broadcasts")
    op.drop_table("admin_broadcasts")
    op.drop_table("administrators")
    op.drop_column("users", "is_active")

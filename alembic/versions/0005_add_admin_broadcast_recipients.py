"""Add explicit recipients to one-off broadcasts.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_broadcast_recipients",
        sa.Column("broadcast_id", sa.BIGINT(), nullable=False),
        sa.Column("user_id", sa.BIGINT(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["broadcast_id"], ["admin_broadcasts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("broadcast_id", "user_id"),
    )
    op.create_index(
        "admin_broadcast_recipients_user_id_idx",
        "admin_broadcast_recipients",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("admin_broadcast_recipients_user_id_idx", table_name="admin_broadcast_recipients")
    op.drop_table("admin_broadcast_recipients")

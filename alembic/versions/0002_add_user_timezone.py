"""Add a fixed UTC offset to users.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "timezone_offset_minutes",
            sa.SMALLINT(),
            server_default=sa.text("420"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "users_timezone_offset_minutes_range",
        "users",
        "timezone_offset_minutes BETWEEN -720 AND 840",
    )


def downgrade() -> None:
    op.drop_constraint("users_timezone_offset_minutes_range", "users", type_="check")
    op.drop_column("users", "timezone_offset_minutes")

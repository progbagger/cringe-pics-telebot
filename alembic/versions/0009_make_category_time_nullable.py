"""Allow subscription types without a schedule.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "subscription_types",
        "time",
        existing_type=sa.Time(timezone=False),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "subscription_types",
        "time",
        existing_type=sa.Time(timezone=False),
        nullable=False,
    )

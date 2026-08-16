"""Store category schedules as local wall-clock times.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "subscription_types",
        "time",
        existing_type=sa.TIME(timezone=True),
        type_=sa.TIME(timezone=False),
        existing_nullable=False,
        postgresql_using='"time"::time without time zone',
    )


def downgrade() -> None:
    op.alter_column(
        "subscription_types",
        "time",
        existing_type=sa.TIME(timezone=False),
        type_=sa.TIME(timezone=True),
        existing_nullable=False,
        postgresql_using="\"time\" AT TIME ZONE 'UTC'",
    )

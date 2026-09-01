"""Add weekdays to subscription type schedules.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription_types",
        sa.Column("weekdays", sa.SMALLINT(), server_default=sa.text("127"), nullable=False),
    )
    op.create_check_constraint(
        "subscription_types_weekdays_valid",
        "subscription_types",
        "weekdays BETWEEN 1 AND 127",
    )


def downgrade() -> None:
    op.drop_constraint(
        "subscription_types_weekdays_valid",
        "subscription_types",
        type_="check",
    )
    op.drop_column("subscription_types", "weekdays")

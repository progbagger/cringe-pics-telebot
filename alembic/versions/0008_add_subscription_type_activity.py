"""Add activity state to subscription types.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription_types",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.execute(sa.text("UPDATE subscription_types SET is_active = true"))


def downgrade() -> None:
    op.drop_column("subscription_types", "is_active")

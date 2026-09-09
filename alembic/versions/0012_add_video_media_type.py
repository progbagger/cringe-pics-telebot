"""Add Telegram video media type.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "category_media_telegram_media_type_values"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "category_media", type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "category_media",
        "telegram_media_type IN ('photo', 'animation', 'video')",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "category_media", type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "category_media",
        "telegram_media_type IN ('photo', 'animation')",
    )

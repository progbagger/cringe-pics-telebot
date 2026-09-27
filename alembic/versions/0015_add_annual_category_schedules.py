"""Add annual category schedules and user birthdays.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANNUAL_DATE_CHECK = """
(schedule_kind = 'annual_date' AND
 annual_month IS NOT NULL AND
 annual_day IS NOT NULL AND
 annual_month BETWEEN 1 AND 12 AND
 annual_day BETWEEN 1 AND CASE
     WHEN annual_month = 2 THEN 29
     WHEN annual_month IN (4, 6, 9, 11) THEN 30
     ELSE 31
 END)
OR
(schedule_kind IN ('weekly', 'annual_birthday') AND
 annual_month IS NULL AND annual_day IS NULL)
"""

_BIRTHDATE_CHECK = """
(birth_month IS NULL AND birth_day IS NULL AND birthdate_source IS NULL)
OR
(birth_month IS NOT NULL AND
 birth_day IS NOT NULL AND
 birthdate_source IS NOT NULL AND
 birth_month BETWEEN 1 AND 12 AND
 birth_day BETWEEN 1 AND CASE
     WHEN birth_month = 2 THEN 29
     WHEN birth_month IN (4, 6, 9, 11) THEN 30
     ELSE 31
 END)
"""


def upgrade() -> None:
    schedule_kind = postgresql.ENUM(
        "weekly",
        "annual_date",
        "annual_birthday",
        name="subscription_schedule_kind",
        create_type=False,
    )
    birthday_source = postgresql.ENUM(
        "telegram",
        "manual",
        name="user_birthday_source",
        create_type=False,
    )
    schedule_kind.create(op.get_bind())
    birthday_source.create(op.get_bind())

    op.add_column(
        "subscription_types",
        sa.Column("schedule_kind", schedule_kind, server_default="weekly", nullable=False),
    )
    op.add_column("subscription_types", sa.Column("annual_month", sa.SMALLINT(), nullable=True))
    op.add_column("subscription_types", sa.Column("annual_day", sa.SMALLINT(), nullable=True))
    op.create_check_constraint(
        "subscription_types_annual_date_valid",
        "subscription_types",
        _ANNUAL_DATE_CHECK,
    )

    op.add_column("users", sa.Column("birth_month", sa.SMALLINT(), nullable=True))
    op.add_column("users", sa.Column("birth_day", sa.SMALLINT(), nullable=True))
    op.add_column("users", sa.Column("birthdate_source", birthday_source, nullable=True))
    op.create_check_constraint("users_birthdate_valid", "users", _BIRTHDATE_CHECK)


def downgrade() -> None:
    op.drop_constraint("users_birthdate_valid", "users", type_="check")
    op.drop_column("users", "birthdate_source")
    op.drop_column("users", "birth_day")
    op.drop_column("users", "birth_month")

    op.drop_constraint("subscription_types_annual_date_valid", "subscription_types", type_="check")
    op.drop_column("subscription_types", "annual_day")
    op.drop_column("subscription_types", "annual_month")
    op.drop_column("subscription_types", "schedule_kind")

    postgresql.ENUM(name="user_birthday_source").drop(op.get_bind())
    postgresql.ENUM(name="subscription_schedule_kind").drop(op.get_bind())

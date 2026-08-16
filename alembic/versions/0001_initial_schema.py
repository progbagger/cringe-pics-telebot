"""Create the initial application schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.BIGINT(), autoincrement=False, nullable=False),
            sa.Column("created_at", sa.TIME(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not inspector.has_table("subscription_types"):
        op.create_table(
            "subscription_types",
            sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
            sa.Column("name", sa.VARCHAR(), nullable=False),
            sa.Column("time", sa.TIME(timezone=True), nullable=False),
            sa.Column("s3_directory_path", sa.VARCHAR(), nullable=False),
            sa.Column("created_at", sa.TIME(timezone=True), nullable=False),
            sa.Column("updated_at", sa.TIME(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )

    if not inspector.has_table("subscriptions"):
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
            sa.Column("subscription_type_id", sa.BIGINT(), nullable=False),
            sa.Column("user_id", sa.BIGINT(), nullable=False),
            sa.Column("created_at", sa.TIME(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["subscription_type_id"], ["subscription_types.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("subscriptions_user_id_idx", "subscriptions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("subscriptions_user_id_idx", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("subscription_types")
    op.drop_table("users")

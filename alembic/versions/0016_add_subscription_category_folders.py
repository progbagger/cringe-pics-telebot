"""Add subscription category folders.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_category_folders",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("name", sa.VARCHAR(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "btrim(name) <> ''",
            name="subscription_category_folders_name_nonempty",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="subscription_category_folders_name_key"),
    )
    op.create_table(
        "subscription_category_folder_members",
        sa.Column("folder_id", sa.BIGINT(), nullable=False),
        sa.Column("subscription_type_id", sa.BIGINT(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["subscription_category_folders.id"],
            name="subscription_category_folder_members_folder_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_type_id"],
            ["subscription_types.id"],
            name="subscription_category_folder_members_subscription_type_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "folder_id",
            "subscription_type_id",
            name="subscription_category_folder_members_pkey",
        ),
        sa.UniqueConstraint(
            "subscription_type_id",
            name="subscription_category_folder_members_subscription_type_key",
        ),
    )

    op.execute(
        """
        DELETE FROM subscriptions AS duplicate
        USING subscriptions AS original
        WHERE duplicate.user_id = original.user_id
          AND duplicate.subscription_type_id = original.subscription_type_id
          AND duplicate.id > original.id
        """
    )
    op.create_unique_constraint(
        "subscriptions_user_type_key",
        "subscriptions",
        ["user_id", "subscription_type_id"],
    )


def downgrade() -> None:
    op.drop_constraint("subscriptions_user_type_key", "subscriptions", type_="unique")
    op.drop_table("subscription_category_folder_members")
    op.drop_table("subscription_category_folders")

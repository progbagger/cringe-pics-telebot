"""Add persistent category media catalog.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIN_POSTGRES_VERSION = 180000


def upgrade() -> None:
    _require_postgres_18()
    op.create_table(
        "category_media",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("subscription_type_id", sa.BIGINT(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("telegram_media_type", sa.Text(), nullable=False),
        sa.Column("telegram_file_id", sa.Text(), nullable=True),
        sa.Column("telegram_file_unique_id", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            sa.Computed(
                "CASE WHEN NOT is_active THEN 'inactive' WHEN telegram_file_id IS NULL THEN 'pending' ELSE 'ready' END",
                persisted=None,
            ),
            nullable=True,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_path <> ''", name="category_media_source_path_nonempty"),
        sa.CheckConstraint("source_revision <> ''", name="category_media_source_revision_nonempty"),
        sa.CheckConstraint(
            "telegram_media_type IN ('photo', 'animation')",
            name="category_media_telegram_media_type_values",
        ),
        sa.CheckConstraint(
            "(telegram_file_id IS NULL AND telegram_file_unique_id IS NULL AND materialized_at IS NULL) OR "
            "(telegram_file_id IS NOT NULL AND telegram_file_unique_id IS NOT NULL AND materialized_at IS NOT NULL)",
            name="category_media_materialization_consistent",
        ),
        sa.ForeignKeyConstraint(["subscription_type_id"], ["subscription_types.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_type_id",
            "source_path",
            name="category_media_subscription_type_source_path_key",
        ),
    )
    op.create_index(
        "category_media_active_category_idx",
        "category_media",
        ["subscription_type_id", "is_active", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("category_media_active_category_idx", table_name="category_media")
    op.drop_table("category_media")


def _require_postgres_18() -> None:
    version = int(op.get_bind().exec_driver_sql("SHOW server_version_num").scalar_one())
    if version < MIN_POSTGRES_VERSION:
        raise RuntimeError(
            "Migration 0007 requires PostgreSQL 18 or newer for virtual generated columns; "
            f"server_version_num is {version}"
        )

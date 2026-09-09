"""Add contextual search aliases to category media.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "media_search_aliases",
        sa.Column("media_id", sa.BIGINT(), nullable=False),
        sa.Column("position", sa.INTEGER(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("position >= 0", name="media_search_aliases_position_nonnegative"),
        sa.CheckConstraint("btrim(alias) <> ''", name="media_search_aliases_alias_nonempty"),
        sa.CheckConstraint(
            "btrim(normalized_alias) <> ''",
            name="media_search_aliases_normalized_alias_nonempty",
        ),
        sa.ForeignKeyConstraint(["media_id"], ["category_media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_id", "position"),
        sa.UniqueConstraint(
            "media_id",
            "normalized_alias",
            name="media_search_aliases_media_normalized_key",
        ),
    )
    op.create_index(
        "media_search_aliases_normalized_trgm_idx",
        "media_search_aliases",
        ["normalized_alias"],
        postgresql_using="gin",
        postgresql_ops={"normalized_alias": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "media_search_aliases_normalized_trgm_idx",
        table_name="media_search_aliases",
        postgresql_using="gin",
    )
    op.drop_table("media_search_aliases")

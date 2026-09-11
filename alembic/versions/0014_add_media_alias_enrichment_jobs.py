"""Add persistent media alias enrichment jobs.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "pending",
        "processing",
        "retry",
        "succeeded",
        "failed",
        "obsolete",
        name="media_alias_enrichment_job_status",
        create_type=False,
    )
    status.create(op.get_bind())
    op.create_table(
        "media_alias_enrichment_jobs",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("media_id", sa.BIGINT(), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=False),
        sa.Column("status", status, server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_token", sa.Text(), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_class", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("btrim(source_revision) <> ''", name="media_alias_enrichment_jobs_revision_nonempty"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND retry_count >= 0",
            name="media_alias_enrichment_jobs_attempt_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL AND leased_until IS NOT NULL) OR "
            "(status <> 'processing' AND lease_token IS NULL AND leased_until IS NULL)",
            name="media_alias_enrichment_jobs_lease_consistent",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'obsolete') AND finished_at IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'failed', 'obsolete') AND finished_at IS NULL)",
            name="media_alias_enrichment_jobs_finished_consistent",
        ),
        sa.CheckConstraint("model IS NULL OR btrim(model) <> ''", name="media_alias_enrichment_jobs_model_nonempty"),
        sa.CheckConstraint(
            "prompt_sha256 IS NULL OR prompt_sha256 ~ '^[0-9a-f]{64}$'",
            name="media_alias_enrichment_jobs_prompt_sha256_format",
        ),
        sa.ForeignKeyConstraint(["media_id"], ["category_media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id",
            "source_revision",
            name="media_alias_enrichment_jobs_media_revision_key",
        ),
    )
    op.create_index(
        "media_alias_enrichment_jobs_available_idx",
        "media_alias_enrichment_jobs",
        ["available_at", "id"],
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )
    op.create_index(
        "media_alias_enrichment_jobs_processing_lease_idx",
        "media_alias_enrichment_jobs",
        ["leased_until", "id"],
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "media_alias_enrichment_jobs_status_idx",
        "media_alias_enrichment_jobs",
        ["status", "id"],
    )


def downgrade() -> None:
    op.drop_index("media_alias_enrichment_jobs_status_idx", table_name="media_alias_enrichment_jobs")
    op.drop_index("media_alias_enrichment_jobs_processing_lease_idx", table_name="media_alias_enrichment_jobs")
    op.drop_index("media_alias_enrichment_jobs_available_idx", table_name="media_alias_enrichment_jobs")
    op.drop_table("media_alias_enrichment_jobs")
    postgresql.ENUM(name="media_alias_enrichment_job_status").drop(op.get_bind())

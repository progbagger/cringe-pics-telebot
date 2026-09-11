from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row, select, text, update

from .connection import get_connection
from .entities.media_alias_enrichment import MediaAliasEnrichmentJob, MediaAliasEnrichmentJobStatus
from .tables import media_alias_enrichment_jobs


async def enqueue_media_alias_enrichment_jobs(
    *,
    subscription_type_id: int,
    model: str,
    prompt_sha256: str,
    enqueued_at: datetime | None = None,
) -> int:
    now = enqueued_at or datetime.now(UTC)
    async with get_connection() as conn:
        await conn.execute(
            text(
                """
                UPDATE media_alias_enrichment_jobs AS jobs
                SET status = 'obsolete',
                    lease_token = NULL,
                    leased_until = NULL,
                    result_class = CASE
                        WHEN NOT media.is_active THEN 'inactive'
                        WHEN jobs.source_revision <> media.source_revision THEN 'stale_revision'
                        ELSE 'aliases_present'
                    END,
                    updated_at = :now,
                    finished_at = :now
                FROM category_media AS media
                WHERE jobs.media_id = media.id
                  AND media.subscription_type_id = :subscription_type_id
                  AND jobs.status IN ('pending', 'processing', 'retry')
                  AND (
                      NOT media.is_active
                      OR jobs.source_revision <> media.source_revision
                      OR EXISTS (
                          SELECT 1
                          FROM media_search_aliases AS aliases
                          WHERE aliases.media_id = media.id
                      )
                  )
                """
            ),
            {"subscription_type_id": subscription_type_id, "now": now},
        )
        rows = (
            await conn.execute(
                text(
                    """
                    INSERT INTO media_alias_enrichment_jobs(
                        media_id,
                        source_revision,
                        status,
                        attempt_count,
                        retry_count,
                        available_at,
                        model,
                        prompt_sha256,
                        created_at,
                        updated_at
                    )
                    SELECT
                        media.id,
                        media.source_revision,
                        'pending',
                        0,
                        0,
                        :now,
                        :model,
                        :prompt_sha256,
                        :now,
                        :now
                    FROM category_media AS media
                    WHERE media.subscription_type_id = :subscription_type_id
                      AND media.is_active
                      AND NOT EXISTS (
                          SELECT 1
                          FROM media_search_aliases AS aliases
                          WHERE aliases.media_id = media.id
                      )
                    ON CONFLICT (media_id, source_revision) DO UPDATE
                    SET status = 'pending',
                        retry_count = 0,
                        available_at = EXCLUDED.available_at,
                        lease_token = NULL,
                        leased_until = NULL,
                        model = EXCLUDED.model,
                        prompt_sha256 = EXCLUDED.prompt_sha256,
                        result_class = NULL,
                        last_error = NULL,
                        updated_at = EXCLUDED.updated_at,
                        finished_at = NULL
                    WHERE media_alias_enrichment_jobs.status IN ('succeeded', 'failed', 'obsolete')
                    RETURNING id
                    """
                ),
                {
                    "subscription_type_id": subscription_type_id,
                    "model": model,
                    "prompt_sha256": prompt_sha256,
                    "now": now,
                },
            )
        ).all()
    return len(rows)


async def claim_media_alias_enrichment_jobs(
    *,
    limit: int,
    lease_token: str,
    lease_ttl: timedelta,
    model: str,
    prompt_sha256: str,
    claimed_at: datetime | None = None,
) -> list[MediaAliasEnrichmentJob]:
    if limit <= 0:
        raise ValueError("Media alias enrichment claim limit must be positive")
    if not lease_token:
        raise ValueError("Media alias enrichment lease token must not be empty")
    if lease_ttl.total_seconds() <= 0:
        raise ValueError("Media alias enrichment lease TTL must be positive")

    now = claimed_at or datetime.now(UTC)
    leased_until = now + lease_ttl
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT id
                        FROM media_alias_enrichment_jobs
                        WHERE (
                            status IN ('pending', 'retry')
                            AND available_at <= :now
                        ) OR (
                            status = 'processing'
                            AND leased_until <= :now
                        )
                        ORDER BY COALESCE(leased_until, available_at), id
                        FOR UPDATE SKIP LOCKED
                        LIMIT :limit
                    )
                    UPDATE media_alias_enrichment_jobs AS jobs
                    SET status = 'processing',
                        attempt_count = jobs.attempt_count + 1,
                        retry_count = jobs.retry_count + 1,
                        lease_token = :lease_token,
                        leased_until = :leased_until,
                        model = :model,
                        prompt_sha256 = :prompt_sha256,
                        result_class = NULL,
                        last_error = NULL,
                        updated_at = :now,
                        finished_at = NULL
                    FROM candidates
                    WHERE jobs.id = candidates.id
                    RETURNING jobs.*
                    """
                ),
                {
                    "limit": limit,
                    "lease_token": lease_token,
                    "leased_until": leased_until,
                    "model": model,
                    "prompt_sha256": prompt_sha256,
                    "now": now,
                },
            )
        ).all()
    return [_job_from_row(row) for row in rows]


async def refresh_media_alias_enrichment_job_lease(
    *,
    job_id: int,
    lease_token: str,
    leased_until: datetime,
    refreshed_at: datetime | None = None,
) -> bool:
    now = refreshed_at or datetime.now(UTC)
    async with get_connection() as conn:
        result = await conn.execute(
            update(media_alias_enrichment_jobs)
            .where(media_alias_enrichment_jobs.c.id == job_id)
            .where(media_alias_enrichment_jobs.c.status == MediaAliasEnrichmentJobStatus.processing)
            .where(media_alias_enrichment_jobs.c.lease_token == lease_token)
            .values(leased_until=leased_until, updated_at=now)
            .returning(media_alias_enrichment_jobs.c.id)
        )
    return result.one_or_none() is not None


async def get_media_alias_enrichment_jobs(
    *,
    media_ids: Sequence[int] | None = None,
) -> list[MediaAliasEnrichmentJob]:
    if media_ids is not None and not media_ids:
        return []
    query = select(media_alias_enrichment_jobs).order_by(media_alias_enrichment_jobs.c.id)
    if media_ids is not None:
        query = query.where(media_alias_enrichment_jobs.c.media_id.in_(media_ids))
    async with get_connection() as conn:
        rows = (await conn.execute(query)).all()
    return [_job_from_row(row) for row in rows]


def _job_from_row(row: Row[Any]) -> MediaAliasEnrichmentJob:
    return MediaAliasEnrichmentJob(
        id=row.id,
        media_id=row.media_id,
        source_revision=row.source_revision,
        status=MediaAliasEnrichmentJobStatus(row.status),
        attempt_count=row.attempt_count,
        retry_count=row.retry_count,
        available_at=row.available_at,
        lease_token=row.lease_token,
        leased_until=row.leased_until,
        model=row.model,
        prompt_sha256=row.prompt_sha256,
        result_class=row.result_class,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )

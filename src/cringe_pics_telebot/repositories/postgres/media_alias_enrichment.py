from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row, case, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from .connection import get_connection
from .entities.media_alias_enrichment import (
    MediaAliasEnrichmentJob,
    MediaAliasEnrichmentJobStatus,
    MediaAliasEnrichmentQueueCounts,
)
from .tables import category_media, media_alias_enrichment_jobs, media_search_aliases

_UNFINISHED_STATUSES = (
    MediaAliasEnrichmentJobStatus.pending,
    MediaAliasEnrichmentJobStatus.processing,
    MediaAliasEnrichmentJobStatus.retry,
)
_REOPENABLE_STATUSES = (
    MediaAliasEnrichmentJobStatus.succeeded,
    MediaAliasEnrichmentJobStatus.failed,
    MediaAliasEnrichmentJobStatus.obsolete,
)


async def enqueue_media_alias_enrichment_jobs(
    *,
    subscription_type_id: int,
    model: str,
    prompt_sha256: str,
    enqueued_at: datetime | None = None,
    media_ids: Sequence[int] | None = None,
) -> int:
    if media_ids is not None and not media_ids:
        return 0
    now = enqueued_at or datetime.now(UTC)
    media_filter = category_media.c.subscription_type_id == subscription_type_id
    if media_ids is not None:
        media_filter &= category_media.c.id.in_(media_ids)
    aliases_exist = exists(
        select(media_search_aliases.c.media_id).where(media_search_aliases.c.media_id == category_media.c.id)
    )
    async with get_connection() as conn:
        await conn.execute(
            update(media_alias_enrichment_jobs)
            .where(media_alias_enrichment_jobs.c.media_id == category_media.c.id)
            .where(media_filter)
            .where(media_alias_enrichment_jobs.c.status.in_(_UNFINISHED_STATUSES))
            .where(
                or_(
                    category_media.c.is_active.is_(False),
                    media_alias_enrichment_jobs.c.source_revision != category_media.c.source_revision,
                    aliases_exist,
                )
            )
            .values(
                status=MediaAliasEnrichmentJobStatus.obsolete,
                lease_token=None,
                leased_until=None,
                result_class=case(
                    (category_media.c.is_active.is_(False), "inactive"),
                    (
                        media_alias_enrichment_jobs.c.source_revision != category_media.c.source_revision,
                        "stale_revision",
                    ),
                    else_="aliases_present",
                ),
                updated_at=now,
                finished_at=now,
            )
        )
        pending = select(
            category_media.c.id,
            category_media.c.source_revision,
            literal(MediaAliasEnrichmentJobStatus.pending, type_=media_alias_enrichment_jobs.c.status.type),
            literal(0),
            literal(0),
            literal(now),
            literal(model),
            literal(prompt_sha256),
            literal(now),
            literal(now),
        ).where(
            media_filter,
            category_media.c.is_active.is_(True),
            ~aliases_exist,
        )
        statement = insert(media_alias_enrichment_jobs).from_select(
            (
                "media_id",
                "source_revision",
                "status",
                "attempt_count",
                "retry_count",
                "available_at",
                "model",
                "prompt_sha256",
                "created_at",
                "updated_at",
            ),
            pending,
        )
        excluded = statement.excluded
        rows = (
            await conn.execute(
                statement.on_conflict_do_update(
                    constraint="media_alias_enrichment_jobs_media_revision_key",
                    set_={
                        "status": MediaAliasEnrichmentJobStatus.pending,
                        "retry_count": 0,
                        "available_at": excluded.available_at,
                        "lease_token": None,
                        "leased_until": None,
                        "model": excluded.model,
                        "prompt_sha256": excluded.prompt_sha256,
                        "result_class": None,
                        "last_error": None,
                        "updated_at": excluded.updated_at,
                        "finished_at": None,
                    },
                    where=media_alias_enrichment_jobs.c.status.in_(_REOPENABLE_STATUSES),
                ).returning(media_alias_enrichment_jobs.c.id)
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
    candidates = (
        select(media_alias_enrichment_jobs.c.id)
        .where(
            or_(
                (
                    media_alias_enrichment_jobs.c.status.in_(
                        (MediaAliasEnrichmentJobStatus.pending, MediaAliasEnrichmentJobStatus.retry)
                    )
                    & (media_alias_enrichment_jobs.c.available_at <= now)
                ),
                (media_alias_enrichment_jobs.c.status == MediaAliasEnrichmentJobStatus.processing)
                & (media_alias_enrichment_jobs.c.leased_until <= now),
            )
        )
        .order_by(
            func.coalesce(
                media_alias_enrichment_jobs.c.leased_until,
                media_alias_enrichment_jobs.c.available_at,
            ),
            media_alias_enrichment_jobs.c.id,
        )
        .with_for_update(skip_locked=True)
        .limit(limit)
        .cte("media_alias_enrichment_candidates")
    )
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                update(media_alias_enrichment_jobs)
                .where(media_alias_enrichment_jobs.c.id == candidates.c.id)
                .values(
                    status=MediaAliasEnrichmentJobStatus.processing,
                    attempt_count=media_alias_enrichment_jobs.c.attempt_count + 1,
                    retry_count=media_alias_enrichment_jobs.c.retry_count + 1,
                    lease_token=lease_token,
                    leased_until=leased_until,
                    model=model,
                    prompt_sha256=prompt_sha256,
                    result_class=None,
                    last_error=None,
                    updated_at=now,
                    finished_at=None,
                )
                .returning(media_alias_enrichment_jobs)
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
            .where(media_alias_enrichment_jobs.c.leased_until > now)
            .values(leased_until=leased_until, updated_at=now)
            .returning(media_alias_enrichment_jobs.c.id)
        )
    return result.one_or_none() is not None


async def get_media_alias_enrichment_job(
    job_id: int,
    *,
    with_for_update: bool = False,
) -> MediaAliasEnrichmentJob | None:
    query = select(media_alias_enrichment_jobs).where(media_alias_enrichment_jobs.c.id == job_id)
    if with_for_update:
        query = query.with_for_update()
    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()
    return _job_from_row(row) if row is not None else None


async def finish_media_alias_enrichment_job(
    *,
    job_id: int,
    lease_token: str,
    status: MediaAliasEnrichmentJobStatus,
    result_class: str,
    finished_at: datetime,
    available_at: datetime | None = None,
    last_error: str | None = None,
) -> bool:
    if status not in (
        MediaAliasEnrichmentJobStatus.retry,
        MediaAliasEnrichmentJobStatus.succeeded,
        MediaAliasEnrichmentJobStatus.failed,
        MediaAliasEnrichmentJobStatus.obsolete,
    ):
        raise ValueError("Enrichment completion must be retry or a terminal status")
    async with get_connection() as conn:
        result = await conn.execute(
            update(media_alias_enrichment_jobs)
            .where(media_alias_enrichment_jobs.c.id == job_id)
            .where(media_alias_enrichment_jobs.c.status == MediaAliasEnrichmentJobStatus.processing)
            .where(media_alias_enrichment_jobs.c.lease_token == lease_token)
            .where(media_alias_enrichment_jobs.c.leased_until > finished_at)
            .values(
                status=status,
                lease_token=None,
                leased_until=None,
                available_at=available_at or finished_at,
                result_class=result_class,
                last_error=last_error,
                updated_at=finished_at,
                finished_at=None if status == MediaAliasEnrichmentJobStatus.retry else finished_at,
            )
            .returning(media_alias_enrichment_jobs.c.id)
        )
    return result.one_or_none() is not None


async def get_media_alias_enrichment_queue_counts(*, now: datetime) -> MediaAliasEnrichmentQueueCounts:
    jobs = media_alias_enrichment_jobs.c
    available = (
        select(func.count())
        .where(
            jobs.status.in_((MediaAliasEnrichmentJobStatus.pending, MediaAliasEnrichmentJobStatus.retry)),
            jobs.available_at <= now,
        )
        .scalar_subquery()
    )
    expired = (
        select(func.count())
        .where(jobs.status == MediaAliasEnrichmentJobStatus.processing, jobs.leased_until <= now)
        .scalar_subquery()
    )
    failed = select(func.count()).where(jobs.status == MediaAliasEnrichmentJobStatus.failed).scalar_subquery()
    async with get_connection() as conn:
        row = (
            await conn.execute(
                select(
                    available.label("available"),
                    expired.label("expired_processing"),
                    failed.label("failed"),
                )
            )
        ).one()
    return MediaAliasEnrichmentQueueCounts(
        available=row.available,
        expired_processing=row.expired_processing,
        failed=row.failed,
    )


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

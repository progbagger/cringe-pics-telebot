from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row

from .connection import get_connection
from .entities.category_media import (
    CategoryMedia,
    CategoryMediaSource,
    CategoryMediaStatus,
    TelegramMediaType,
)
from .tables import category_media


async def get_category_media_by_subscription_types(
    subscription_type_ids: Sequence[int],
    *,
    active_only: bool = True,
    ready_only: bool = False,
    with_for_update: bool = False,
) -> list[CategoryMedia]:
    if not subscription_type_ids:
        return []

    query = select(category_media).where(category_media.c.subscription_type_id.in_(subscription_type_ids))
    if active_only:
        query = query.where(category_media.c.is_active.is_(True))
    if ready_only:
        query = query.where(category_media.c.status == CategoryMediaStatus.ready)
    query = query.order_by(category_media.c.subscription_type_id, category_media.c.id)
    if with_for_update:
        query = query.with_for_update()

    async with get_connection() as conn:
        rows = (await conn.execute(query)).all()
    return [_category_media_from_row(row) for row in rows]


async def get_category_media(media_id: int, *, with_for_update: bool = False) -> CategoryMedia | None:
    query = select(category_media).where(category_media.c.id == media_id)
    if with_for_update:
        query = query.with_for_update()
    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()
    return _category_media_from_row(row) if row is not None else None


async def upsert_category_media_snapshot(
    *,
    subscription_type_id: int,
    sources: Sequence[CategoryMediaSource],
    seen_at: datetime | None = None,
) -> None:
    sources_by_path = {source.source_path: source for source in sources}
    unique_sources = tuple(sources_by_path.values())
    now = seen_at or datetime.now(UTC)
    async with get_connection() as conn:
        if unique_sources:
            statement = insert(category_media).values(
                [
                    {
                        "subscription_type_id": subscription_type_id,
                        "source_path": source.source_path,
                        "source_revision": source.source_revision,
                        "name": source.name,
                        "mime_type": source.mime_type,
                        "telegram_media_type": source.telegram_media_type,
                        "is_active": True,
                        "last_seen_at": now,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for source in unique_sources
                ]
            )
            excluded = statement.excluded
            revision_changed = category_media.c.source_revision != excluded.source_revision
            metadata_changed = or_(
                category_media.c.name != excluded.name,
                category_media.c.mime_type != excluded.mime_type,
                category_media.c.telegram_media_type != excluded.telegram_media_type,
            )
            row_changed = or_(
                revision_changed,
                metadata_changed,
                category_media.c.is_active.is_(False),
            )
            await conn.execute(
                statement.on_conflict_do_update(
                    constraint="category_media_subscription_type_source_path_key",
                    set_={
                        "source_revision": excluded.source_revision,
                        "name": excluded.name,
                        "mime_type": excluded.mime_type,
                        "telegram_media_type": excluded.telegram_media_type,
                        "telegram_file_id": case(
                            (revision_changed, None),
                            else_=category_media.c.telegram_file_id,
                        ),
                        "telegram_file_unique_id": case(
                            (revision_changed, None),
                            else_=category_media.c.telegram_file_unique_id,
                        ),
                        "materialized_at": case(
                            (revision_changed, None),
                            else_=category_media.c.materialized_at,
                        ),
                        "is_active": True,
                        "last_seen_at": now,
                        "updated_at": case(
                            (row_changed, now),
                            else_=category_media.c.updated_at,
                        ),
                    },
                )
            )


async def deactivate_category_media_missing_from_snapshot(
    *,
    subscription_type_id: int,
    source_paths: Collection[str],
    seen_at: datetime | None = None,
) -> int:
    now = seen_at or datetime.now(UTC)
    query = (
        update(category_media)
        .where(category_media.c.subscription_type_id == subscription_type_id)
        .where(category_media.c.is_active.is_(True))
    )
    if source_paths:
        query = query.where(category_media.c.source_path.not_in(source_paths))
    async with get_connection() as conn:
        deactivated = await conn.execute(query.values(is_active=False, updated_at=now).returning(category_media.c.id))
    return len(deactivated.all())


async def materialize_category_media(
    *,
    media_id: int,
    source_revision: str,
    telegram_file_id: str,
    telegram_file_unique_id: str,
    materialized_at: datetime | None = None,
) -> CategoryMedia | None:
    now = materialized_at or datetime.now(UTC)
    async with get_connection() as conn:
        row = (
            await conn.execute(
                update(category_media)
                .where(category_media.c.id == media_id)
                .where(category_media.c.source_revision == source_revision)
                .where(category_media.c.is_active.is_(True))
                .where(category_media.c.telegram_file_id.is_(None))
                .values(
                    telegram_file_id=telegram_file_id,
                    telegram_file_unique_id=telegram_file_unique_id,
                    materialized_at=now,
                    updated_at=now,
                )
                .returning(category_media)
            )
        ).one_or_none()
    return _category_media_from_row(row) if row is not None else None


async def invalidate_category_media_file_id(
    *,
    media_id: int,
    telegram_file_id: str,
    invalidated_at: datetime | None = None,
) -> CategoryMedia | None:
    now = invalidated_at or datetime.now(UTC)
    async with get_connection() as conn:
        row = (
            await conn.execute(
                update(category_media)
                .where(category_media.c.id == media_id)
                .where(category_media.c.telegram_file_id == telegram_file_id)
                .values(
                    telegram_file_id=None,
                    telegram_file_unique_id=None,
                    materialized_at=None,
                    updated_at=now,
                )
                .returning(category_media)
            )
        ).one_or_none()
    return _category_media_from_row(row) if row is not None else None


def _category_media_from_row(row: Row[Any]) -> CategoryMedia:
    return CategoryMedia(
        id=row.id,
        subscription_type_id=row.subscription_type_id,
        source_path=row.source_path,
        source_revision=row.source_revision,
        name=row.name,
        mime_type=row.mime_type,
        telegram_media_type=TelegramMediaType(row.telegram_media_type),
        telegram_file_id=row.telegram_file_id,
        telegram_file_unique_id=row.telegram_file_unique_id,
        is_active=row.is_active,
        status=CategoryMediaStatus(row.status),
        last_seen_at=row.last_seen_at,
        materialized_at=row.materialized_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

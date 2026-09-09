from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row

from cringe_pics_telebot.entities.search_aliases import SearchAlias

from .connection import get_connection
from .entities.category_media import (
    CategoryMedia,
    CategoryMediaSearchMetadata,
    CategoryMediaSource,
    CategoryMediaStatus,
    TelegramMediaType,
)
from .tables import category_media, media_search_aliases, subscription_types


async def get_category_media_by_subscription_types(
    subscription_type_ids: Sequence[int] | None,
    *,
    active_only: bool = True,
    ready_only: bool = False,
    with_for_update: bool = False,
) -> list[CategoryMedia]:
    if subscription_type_ids is not None and not subscription_type_ids:
        return []

    query = select(category_media)
    if subscription_type_ids is not None:
        query = query.where(category_media.c.subscription_type_id.in_(subscription_type_ids))
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


async def get_category_media_search_metadata(
    media_id: int,
) -> CategoryMediaSearchMetadata | None:
    items = await _get_category_media_search_metadata(media_id=media_id)
    return items[0] if items else None


async def get_category_media_search_metadata_by_subscription_type(
    subscription_type_id: int,
    *,
    active_only: bool = False,
) -> list[CategoryMediaSearchMetadata]:
    return await _get_category_media_search_metadata(
        subscription_type_id=subscription_type_id,
        active_only=active_only,
    )


async def replace_media_search_aliases(media_id: int, aliases: Sequence[SearchAlias]) -> None:
    async with get_connection() as conn:
        await conn.execute(delete(media_search_aliases).where(media_search_aliases.c.media_id == media_id))
        if aliases:
            await conn.execute(
                insert(media_search_aliases).values(
                    [
                        {
                            "media_id": media_id,
                            "position": position,
                            "alias": alias.text,
                            "normalized_alias": alias.normalized,
                        }
                        for position, alias in enumerate(aliases)
                    ]
                )
            )


async def find_category_media_by_search_terms(
    search_terms: Sequence[str],
    *,
    subscription_type_ids: Sequence[int] | None = None,
) -> list[CategoryMedia]:
    if not search_terms or subscription_type_ids is not None and not subscription_type_ids:
        return []

    query = (
        select(category_media)
        .join(
            subscription_types,
            subscription_types.c.id == category_media.c.subscription_type_id,
        )
        .where(
            category_media.c.is_active.is_(True),
            subscription_types.c.is_active.is_(True),
        )
    )
    if subscription_type_ids is not None:
        query = query.where(category_media.c.subscription_type_id.in_(subscription_type_ids))
    for position, search_term in enumerate(search_terms):
        alias_for_term = media_search_aliases.alias(f"media_search_alias_{position}")
        query = query.where(
            exists(
                select(alias_for_term.c.media_id).where(
                    alias_for_term.c.media_id == category_media.c.id,
                    alias_for_term.c.normalized_alias.contains(search_term, autoescape=True),
                )
            )
        )
    query = query.order_by(category_media.c.subscription_type_id, category_media.c.id)

    async with get_connection() as conn:
        rows = (await conn.execute(query)).all()
    return [_category_media_from_row(row) for row in rows]


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


async def _get_category_media_search_metadata(
    *,
    media_id: int | None = None,
    subscription_type_id: int | None = None,
    active_only: bool = False,
) -> list[CategoryMediaSearchMetadata]:
    query = (
        select(category_media, media_search_aliases.c.alias.label("search_alias"))
        .outerjoin(media_search_aliases, media_search_aliases.c.media_id == category_media.c.id)
        .order_by(category_media.c.subscription_type_id, category_media.c.id, media_search_aliases.c.position)
    )
    if media_id is not None:
        query = query.where(category_media.c.id == media_id)
    if subscription_type_id is not None:
        query = query.where(category_media.c.subscription_type_id == subscription_type_id)
    if active_only:
        query = query.where(category_media.c.is_active.is_(True))

    async with get_connection() as conn:
        rows = (await conn.execute(query)).all()

    media_by_id: dict[int, tuple[CategoryMedia, list[str]]] = {}
    for row in rows:
        if (item := media_by_id.get(row.id)) is None:
            item = (_category_media_from_row(row), [])
            media_by_id[row.id] = item
        _, aliases = item
        if row.search_alias is not None:
            aliases.append(row.search_alias)
    return [
        CategoryMediaSearchMetadata(media=media, search_aliases=tuple(aliases))
        for media, aliases in media_by_id.values()
    ]


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

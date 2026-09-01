from collections.abc import Sequence
from datetime import time
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row

from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays

from .connection import get_connection
from .entities.subscription_type import CreateSubscriptionType, SubscriptionType
from .tables import subscription_types


async def get_all_subscription_types() -> list[SubscriptionType]:
    return await _get_subscription_types(active_only=False)


async def get_active_subscription_types() -> list[SubscriptionType]:
    return await _get_subscription_types(active_only=True)


async def get_active_scheduled_subscription_types() -> list[SubscriptionType]:
    return await _get_subscription_types(active_only=True, scheduled_only=True)


async def get_subscription_types() -> list[SubscriptionType]:
    return await get_all_subscription_types()


async def get_subscription_type(subscription_type_id: int) -> SubscriptionType | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(select(subscription_types).where(subscription_types.c.id == subscription_type_id))
        ).one_or_none()
        return _subscription_type_from_row(row) if row is not None else None


async def get_subscription_type_by_name(name: str) -> SubscriptionType | None:
    async with get_connection() as conn:
        row = (await conn.execute(select(subscription_types).where(subscription_types.c.name == name))).one_or_none()
    return _subscription_type_from_row(row) if row is not None else None


async def create_subscription_type(data: CreateSubscriptionType) -> SubscriptionType | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                insert(subscription_types)
                .values(
                    name=data.name,
                    time=data.time,
                    weekdays=data.weekdays.mask,
                    s3_directory_path=data.s3_directory_path,
                    search_aliases=list(data.search_aliases),
                    is_active=False,
                    created_at=func.now(),
                    updated_at=func.now(),
                )
                .on_conflict_do_nothing(index_elements=[subscription_types.c.name])
                .returning(subscription_types)
            )
        ).one_or_none()
    return _subscription_type_from_row(row) if row is not None else None


async def set_subscription_type_activity(
    subscription_type_id: int,
    *,
    is_active: bool,
) -> SubscriptionType | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                update(subscription_types)
                .where(subscription_types.c.id == subscription_type_id)
                .values(is_active=is_active, updated_at=func.now())
                .returning(subscription_types)
            )
        ).one_or_none()
    return _subscription_type_from_row(row) if row is not None else None


async def get_active_subscription_type(
    subscription_type_id: int,
    *,
    with_for_update: bool = False,
) -> SubscriptionType | None:
    return await _get_active_subscription_type(
        subscription_type_id,
        with_for_update=with_for_update,
        scheduled_only=False,
    )


async def get_active_scheduled_subscription_type(
    subscription_type_id: int,
    *,
    with_for_update: bool = False,
) -> SubscriptionType | None:
    return await _get_active_subscription_type(
        subscription_type_id,
        with_for_update=with_for_update,
        scheduled_only=True,
    )


async def update_subscription_type_time(
    subscription_type_id: int,
    send_time: time | None,
) -> SubscriptionType | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                update(subscription_types)
                .where(subscription_types.c.id == subscription_type_id)
                .values(time=send_time, updated_at=func.now())
                .returning(subscription_types)
            )
        ).one_or_none()
    return _subscription_type_from_row(row) if row is not None else None


async def update_subscription_type_weekdays(
    subscription_type_id: int,
    weekdays: SubscriptionWeekdays,
) -> SubscriptionType | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                update(subscription_types)
                .where(subscription_types.c.id == subscription_type_id)
                .values(weekdays=weekdays.mask, updated_at=func.now())
                .returning(subscription_types)
            )
        ).one_or_none()
    return _subscription_type_from_row(row) if row is not None else None


async def update_subscription_type_search_aliases(
    subscription_type_id: int,
    search_aliases: Sequence[str],
) -> bool:
    async with get_connection() as conn:
        result = await conn.execute(
            update(subscription_types)
            .where(subscription_types.c.id == subscription_type_id)
            .values(
                search_aliases=list(search_aliases),
                updated_at=func.now(),
            )
            .returning(subscription_types.c.id)
        )
        return result.scalar_one_or_none() is not None


async def _get_subscription_types(*, active_only: bool, scheduled_only: bool = False) -> list[SubscriptionType]:
    query = select(subscription_types)
    if active_only:
        query = query.where(subscription_types.c.is_active.is_(True))
    if scheduled_only:
        query = query.where(subscription_types.c.time.is_not(None))

    async with get_connection() as conn:
        rows = (await conn.execute(query)).fetchall()
    return [_subscription_type_from_row(row) for row in rows]


async def _get_active_subscription_type(
    subscription_type_id: int,
    *,
    with_for_update: bool,
    scheduled_only: bool,
) -> SubscriptionType | None:
    query = select(subscription_types).where(
        subscription_types.c.id == subscription_type_id,
        subscription_types.c.is_active.is_(True),
    )
    if scheduled_only:
        query = query.where(subscription_types.c.time.is_not(None))
    if with_for_update:
        query = query.with_for_update()

    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()
    return _subscription_type_from_row(row) if row is not None else None


def _subscription_type_from_row(row: Row[Any]) -> SubscriptionType:
    return SubscriptionType(
        id=row.id,
        name=row.name,
        time=row.time,
        weekdays=SubscriptionWeekdays.from_mask(row.weekdays),
        s3_directory_path=row.s3_directory_path,
        search_aliases=tuple(row.search_aliases),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

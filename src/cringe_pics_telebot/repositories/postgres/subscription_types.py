from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.engine import Row

from .connection import get_connection
from .entities.subscription_type import SubscriptionType
from .tables import subscription_types


async def get_all_subscription_types() -> list[SubscriptionType]:
    return await _get_subscription_types(active_only=False)


async def get_active_subscription_types() -> list[SubscriptionType]:
    return await _get_subscription_types(active_only=True)


async def get_subscription_types() -> list[SubscriptionType]:
    return await get_all_subscription_types()


async def get_subscription_type(subscription_type_id: int) -> SubscriptionType | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(select(subscription_types).where(subscription_types.c.id == subscription_type_id))
        ).one_or_none()
        return _subscription_type_from_row(row) if row is not None else None


async def get_active_subscription_type(
    subscription_type_id: int,
    *,
    with_for_update: bool = False,
) -> SubscriptionType | None:
    query = select(subscription_types).where(
        subscription_types.c.id == subscription_type_id,
        subscription_types.c.is_active.is_(True),
    )
    if with_for_update:
        query = query.with_for_update()

    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()
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


async def _get_subscription_types(*, active_only: bool) -> list[SubscriptionType]:
    query = select(subscription_types)
    if active_only:
        query = query.where(subscription_types.c.is_active.is_(True))

    async with get_connection() as conn:
        rows = (await conn.execute(query)).fetchall()
    return [_subscription_type_from_row(row) for row in rows]


def _subscription_type_from_row(row: Row[Any]) -> SubscriptionType:
    return SubscriptionType(
        id=row.id,
        name=row.name,
        time=row.time,
        s3_directory_path=row.s3_directory_path,
        search_aliases=tuple(row.search_aliases),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

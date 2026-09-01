from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from .connection import get_connection
from .tables import user_media_cycle_entries, user_media_cycle_states


@dataclass(frozen=True, slots=True)
class UserMediaCycleEntries:
    shown_media_ids: frozenset[int]
    reserved_media_ids: frozenset[int]


async def lock_user_media_cycle(*, user_id: int, subscription_type_id: int, updated_at: datetime) -> int | None:
    async with get_connection() as conn:
        await conn.execute(
            insert(user_media_cycle_states)
            .values(
                user_id=user_id,
                subscription_type_id=subscription_type_id,
                updated_at=updated_at,
            )
            .on_conflict_do_nothing()
        )
        row = (
            await conn.execute(
                select(user_media_cycle_states.c.last_media_id)
                .where(
                    user_media_cycle_states.c.user_id == user_id,
                    user_media_cycle_states.c.subscription_type_id == subscription_type_id,
                )
                .with_for_update()
            )
        ).one()
        return row.last_media_id


async def delete_expired_user_media_cycle_reservations(
    *,
    user_id: int,
    subscription_type_id: int,
    expired_at: datetime,
) -> None:
    async with get_connection() as conn:
        await conn.execute(
            delete(user_media_cycle_entries).where(
                user_media_cycle_entries.c.user_id == user_id,
                user_media_cycle_entries.c.subscription_type_id == subscription_type_id,
                user_media_cycle_entries.c.reservation_token.is_not(None),
                user_media_cycle_entries.c.reserved_until <= expired_at,
            )
        )


async def get_user_media_cycle_entries(*, user_id: int, subscription_type_id: int) -> UserMediaCycleEntries:
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                select(
                    user_media_cycle_entries.c.media_id,
                    user_media_cycle_entries.c.reservation_token,
                ).where(
                    user_media_cycle_entries.c.user_id == user_id,
                    user_media_cycle_entries.c.subscription_type_id == subscription_type_id,
                )
            )
        ).all()
    return UserMediaCycleEntries(
        shown_media_ids=frozenset(row.media_id for row in rows if row.reservation_token is None),
        reserved_media_ids=frozenset(row.media_id for row in rows if row.reservation_token is not None),
    )


async def reset_user_media_cycle(*, user_id: int, subscription_type_id: int) -> None:
    async with get_connection() as conn:
        await conn.execute(
            delete(user_media_cycle_entries).where(
                user_media_cycle_entries.c.user_id == user_id,
                user_media_cycle_entries.c.subscription_type_id == subscription_type_id,
            )
        )


async def create_user_media_cycle_reservation(
    *,
    user_id: int,
    subscription_type_id: int,
    media_id: int,
    reservation_token: str,
    reserved_until: datetime,
) -> None:
    async with get_connection() as conn:
        await conn.execute(
            insert(user_media_cycle_entries).values(
                user_id=user_id,
                subscription_type_id=subscription_type_id,
                media_id=media_id,
                reservation_token=reservation_token,
                reserved_until=reserved_until,
            )
        )


async def confirm_user_media_cycle_reservation(
    *,
    user_id: int,
    subscription_type_id: int,
    media_id: int,
    reservation_token: str,
    shown_at: datetime,
) -> bool:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                update(user_media_cycle_entries)
                .where(
                    user_media_cycle_entries.c.user_id == user_id,
                    user_media_cycle_entries.c.subscription_type_id == subscription_type_id,
                    user_media_cycle_entries.c.media_id == media_id,
                    user_media_cycle_entries.c.reservation_token == reservation_token,
                )
                .values(
                    reservation_token=None,
                    reserved_until=None,
                    shown_at=shown_at,
                )
                .returning(user_media_cycle_entries.c.media_id)
            )
        ).one_or_none()
        if row is None:
            return False

        await conn.execute(
            update(user_media_cycle_states)
            .where(
                user_media_cycle_states.c.user_id == user_id,
                user_media_cycle_states.c.subscription_type_id == subscription_type_id,
            )
            .values(last_media_id=media_id, updated_at=shown_at)
        )
        return True


async def release_user_media_cycle_reservation(
    *,
    user_id: int,
    subscription_type_id: int,
    media_id: int,
    reservation_token: str,
) -> bool:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                delete(user_media_cycle_entries)
                .where(
                    user_media_cycle_entries.c.user_id == user_id,
                    user_media_cycle_entries.c.subscription_type_id == subscription_type_id,
                    user_media_cycle_entries.c.media_id == media_id,
                    user_media_cycle_entries.c.reservation_token == reservation_token,
                )
                .returning(user_media_cycle_entries.c.media_id)
            )
        ).one_or_none()
        return row is not None

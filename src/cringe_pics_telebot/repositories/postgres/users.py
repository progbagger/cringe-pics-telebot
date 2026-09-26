from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.user_birthday import UserBirthdaySource

from .connection import get_connection
from .entities import User
from .tables import users as users


async def create_user(user_id: int) -> User:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                insert(users)
                .values(id=user_id, is_active=True, created_at=datetime.now(UTC))
                .on_conflict_do_update(
                    index_elements=[users.c.id],
                    set_={"is_active": True},
                )
                .returning(users)
            )
        ).fetchone()
        assert row is not None
        return User(
            id=row.id,
            timezone_offset_minutes=row.timezone_offset_minutes,
            is_active=row.is_active,
            created_at=row.created_at,
            birthday=_birthday_from_row(row),
            birthday_source=_birthday_source_from_row(row),
        )


async def get_active_users() -> list[User]:
    async with get_connection() as conn:
        rows = (await conn.execute(select(users).where(users.c.is_active.is_(True)).order_by(users.c.id))).all()

    return [
        User(
            id=row.id,
            timezone_offset_minutes=row.timezone_offset_minutes,
            is_active=row.is_active,
            created_at=row.created_at,
            birthday=_birthday_from_row(row),
            birthday_source=_birthday_source_from_row(row),
        )
        for row in rows
    ]


async def deactivate_user(user_id: int) -> None:
    async with get_connection() as conn:
        await conn.execute(update(users).where(users.c.id == user_id).values(is_active=False))


async def get_user_timezone_offset(user_id: int) -> int | None:
    async with get_connection() as conn:
        return await conn.scalar(select(users.c.timezone_offset_minutes).where(users.c.id == user_id))


async def get_user_birthday(user_id: int) -> AnnualDate | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(select(users.c.birth_month, users.c.birth_day).where(users.c.id == user_id))
        ).one_or_none()
    if row is None or row.birth_month is None or row.birth_day is None:
        return None
    return AnnualDate(month=row.birth_month, day=row.birth_day)


async def set_user_timezone_offset(*, user_id: int, timezone_offset_minutes: int) -> None:
    async with get_connection() as conn:
        await conn.execute(
            insert(users)
            .values(
                id=user_id,
                timezone_offset_minutes=timezone_offset_minutes,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[users.c.id],
                set_={"timezone_offset_minutes": timezone_offset_minutes},
            )
        )


def _birthday_from_row(row: Any) -> AnnualDate | None:
    birth_month = row.birth_month
    birth_day = row.birth_day
    if birth_month is None or birth_day is None:
        return None
    return AnnualDate(month=birth_month, day=birth_day)


def _birthday_source_from_row(row: Any) -> UserBirthdaySource | None:
    source = row.birthdate_source
    return UserBirthdaySource(source) if source is not None else None

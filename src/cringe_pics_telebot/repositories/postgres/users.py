from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .connection import get_connection
from .entities import User
from .tables import users as users


async def create_user(user_id: int) -> User | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                insert(users).values(id=user_id, created_at=datetime.now(UTC)).on_conflict_do_nothing().returning(users)
            )
        ).fetchone()

        return (
            User(
                id=row.id,
                timezone_offset_minutes=row.timezone_offset_minutes,
                created_at=row.created_at,
            )
            if row
            else None
        )


async def get_user_timezone_offset(user_id: int) -> int | None:
    async with get_connection() as conn:
        return await conn.scalar(select(users.c.timezone_offset_minutes).where(users.c.id == user_id))


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

from datetime import UTC, datetime

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

        return User(id=row.id, created_at=row.created_at) if row else None

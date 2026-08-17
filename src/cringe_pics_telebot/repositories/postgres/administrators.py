from sqlalchemy import select

from .connection import get_connection
from .tables import administrators


async def is_administrator(user_id: int) -> bool:
    async with get_connection() as conn:
        return (
            await conn.scalar(select(administrators.c.user_id).where(administrators.c.user_id == user_id).limit(1))
        ) is not None


from cachetools.func import ttl_cache
from sqlalchemy import select

from .connection import get_connection
from .entities.subscription_type import SubscriptionType
from .tables import subscription_types


@ttl_cache(maxsize=1, ttl=60 * 60 * 24)  # 1 day
async def get_subscription_types() -> list[SubscriptionType]:
    async with get_connection() as conn:
        rows = (await conn.execute(select(subscription_types))).fetchall()

        return [
            SubscriptionType(
                id=row.id,
                name=row.name,
                time=row.time,
                s3_directory_path=row.s3_directory_path,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

from sqlalchemy import delete, insert, select

from .connection import get_connection
from .entities import CreateSubscription, Subscription
from .tables import subscriptions


async def create_subscription(subscription: CreateSubscription) -> Subscription:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                insert(subscriptions)
                .values(
                    subscription_type_id=subscription.subscription_type_id,
                    user_id=subscription.user_id,
                    created_at=subscription.created_at,
                )
                .returning()
            )
        ).fetchone()
        assert row is not None

        return Subscription(
            id=row.id,
            subscription_type_id=row.subscription_type_id,
            user_id=row.user_id,
            created_at=row.created_at,
        )


async def get_user_subscriptions(user_id: int) -> list[Subscription]:
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                select(subscriptions).where(subscriptions.c.user_id == user_id)
            )
        ).fetchall()

        return [
            Subscription(
                id=row.id,
                subscription_type_id=row.subscription_type_id,
                user_id=row.user_id,
                created_at=row.created_at,
            )
            for row in rows
        ]


async def delete_subscription(subscription_id: int) -> Subscription | None:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                delete(subscriptions)
                .where(subscriptions.c.id == subscription_id)
                .returning()
            )
        ).fetchone()
        if row is None:
            return None

        return Subscription(
            id=row.id,
            subscription_type_id=row.subscription_type_id,
            user_id=row.user_id,
            created_at=row.created_at,
        )

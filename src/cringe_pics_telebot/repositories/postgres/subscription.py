from sqlalchemy import delete, insert, select

from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo

from .connection import get_connection
from .entities import CreateSubscription, Subscription
from .tables import subscription_types, subscriptions

st = subscription_types
s = subscriptions


async def create_subscription(subscription: CreateSubscription) -> Subscription:
    async with get_connection() as conn:
        row = (
            await conn.execute(
                insert(s)
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


async def get_user_subscriptions(user_id: int) -> list[SubscriptionInfo]:
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                select(
                    st.c.id,
                    st.c.name,
                    st.c.time,
                    s.c.id.is_not(None).label("subscribed"),
                )
                .select_from(st.outerjoin(s, s.c.subscription_type_id == st.c.id))
                .where(s.c.user_id == user_id)
            )
        ).fetchall()

        return [
            SubscriptionInfo(
                id=row.id,
                name=row.name,
                send_time=row.time,
                subscribed=row.subscribed,
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

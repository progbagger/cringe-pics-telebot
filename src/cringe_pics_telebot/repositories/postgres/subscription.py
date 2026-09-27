from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.subscription_schedule import SubscriptionScheduleKind
from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.entities.user_birthday import UserBirthdaySource

from .connection import get_connection
from .entities import CreateSubscription, Subscription, User
from .tables import subscription_types, subscriptions, users

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
                .on_conflict_do_nothing()
                .returning(s)
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
        us = select(s).where(s.c.user_id == user_id).subquery()
        rows = (
            await conn.execute(
                select(
                    st.c.id,
                    st.c.name,
                    st.c.time,
                    st.c.weekdays,
                    st.c.schedule_kind,
                    st.c.annual_month,
                    st.c.annual_day,
                    us.c.id.is_not(None).label("subscribed"),
                )
                .select_from(st.outerjoin(us, us.c.subscription_type_id == st.c.id))
                .where(st.c.is_active.is_(True), st.c.time.is_not(None))
            )
        ).fetchall()

        return [
            SubscriptionInfo(
                id=row.id,
                name=row.name,
                send_time=row.time,
                weekdays=SubscriptionWeekdays.from_mask(row.weekdays),
                subscribed=row.subscribed,
                schedule_kind=SubscriptionScheduleKind(row.schedule_kind),
                annual_date=(
                    AnnualDate(month=row.annual_month, day=row.annual_day)
                    if row.annual_month is not None and row.annual_day is not None
                    else None
                ),
            )
            for row in rows
        ]


async def get_subscription_users(subscription_type_id: int) -> list[User]:
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                select(users)
                .select_from(s.join(users, users.c.id == s.c.user_id))
                .where(s.c.subscription_type_id == subscription_type_id)
                .distinct()
                .order_by(users.c.id)
            )
        ).fetchall()

        return [
            User(
                id=row.id,
                timezone_offset_minutes=row.timezone_offset_minutes,
                is_active=row.is_active,
                created_at=row.created_at,
                birthday=(
                    AnnualDate(month=row.birth_month, day=row.birth_day)
                    if row.birth_month is not None and row.birth_day is not None
                    else None
                ),
                birthday_source=(
                    UserBirthdaySource(row.birthdate_source) if row.birthdate_source is not None else None
                ),
            )
            for row in rows
        ]


async def delete_subscription(*, user_id: int, subscription_type_id: int) -> None:
    async with get_connection() as conn:
        await conn.execute(
            delete(subscriptions)
            .where(subscriptions.c.user_id == user_id)
            .where(subscriptions.c.subscription_type_id == subscription_type_id)
            .returning()
        )

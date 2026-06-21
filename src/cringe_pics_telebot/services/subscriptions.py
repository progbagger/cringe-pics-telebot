from datetime import timedelta

from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.repositories.postgres import create_subscription, delete_subscription, transaction
from cringe_pics_telebot.repositories.postgres import get_subscription_types as get_subscription_types_pg
from cringe_pics_telebot.repositories.postgres import get_user_subscriptions as get_user_subscriptions_from_pg
from cringe_pics_telebot.repositories.postgres.entities import CreateSubscription
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import SubscriptionType
from cringe_pics_telebot.repositories.postgres.users import create_user
from cringe_pics_telebot.repositories.redis import cached


@cached(ttl=timedelta(minutes=10))
async def get_subscription_types() -> list[SubscriptionType]:
    return await get_subscription_types_pg()


async def get_user_subscriptions(user_id: int) -> list[SubscriptionInfo]:
    return await get_user_subscriptions_from_pg(user_id)


async def subscribe(*, user_id: int, subscription_type_id: int) -> None:
    async with transaction():
        await create_user(user_id)
        await create_subscription(
            CreateSubscription(
                subscription_type_id=subscription_type_id,
                user_id=user_id,
            )
        )


async def unsubscribe(*, user_id: int, subscription_type_id: int) -> None:
    await delete_subscription(
        user_id=user_id,
        subscription_type_id=subscription_type_id,
    )

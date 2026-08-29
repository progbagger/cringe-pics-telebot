from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.repositories.postgres import (
    create_subscription,
    delete_subscription,
    get_active_subscription_type,
    get_active_subscription_types,
    transaction,
)
from cringe_pics_telebot.repositories.postgres import get_subscription_users as get_subscription_users_from_pg
from cringe_pics_telebot.repositories.postgres import get_user_subscriptions as get_user_subscriptions_from_pg
from cringe_pics_telebot.repositories.postgres.entities import CreateSubscription, User
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import SubscriptionType
from cringe_pics_telebot.repositories.postgres.users import create_user


class SubscriptionTypeUnavailableError(LookupError): ...


async def get_subscription_types() -> list[SubscriptionType]:
    return await get_active_subscription_types()


async def get_user_subscriptions(user_id: int) -> list[SubscriptionInfo]:
    return await get_user_subscriptions_from_pg(user_id)


async def get_subscription_users(subscription_type_id: int) -> list[User]:
    return await get_subscription_users_from_pg(subscription_type_id)


async def subscribe(*, user_id: int, subscription_type_id: int) -> None:
    async with transaction():
        if await get_active_subscription_type(subscription_type_id, with_for_update=True) is None:
            raise SubscriptionTypeUnavailableError(subscription_type_id)

        await create_user(user_id)
        await create_subscription(
            CreateSubscription(
                subscription_type_id=subscription_type_id,
                user_id=user_id,
            )
        )


async def unsubscribe(*, user_id: int, subscription_type_id: int) -> None:
    async with transaction():
        await delete_subscription(
            user_id=user_id,
            subscription_type_id=subscription_type_id,
        )

from cringe_pics_telebot.entities.subscriptions import (
    SubscriptionInfo,
)
from cringe_pics_telebot.repositories.postgres import (
    create_subscription,
    delete_subscription,
)
from cringe_pics_telebot.repositories.postgres import (
    get_user_subscriptions as get_user_subscriptions_from_pg,
)
from cringe_pics_telebot.repositories.postgres.entities import CreateSubscription


async def get_user_subscriptions(user_id: int) -> list[SubscriptionInfo]:
    return await get_user_subscriptions_from_pg(user_id)


async def subscribe(*, user_id: int, subscription_type_id: int) -> None:
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

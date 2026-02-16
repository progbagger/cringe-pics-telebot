from cringe_pics_telebot.entities.subscriptions import (
    SubscriptionInfo,
)
from cringe_pics_telebot.repositories.postgres import (
    get_user_subscriptions as get_user_subscriptions_from_pg,
)


async def get_user_subscriptions(user_id: int) -> list[SubscriptionInfo]:
    return await get_user_subscriptions_from_pg(user_id)

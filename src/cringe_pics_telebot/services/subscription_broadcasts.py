import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time, timedelta, timezone

from aiogram import Bot

from cringe_pics_telebot.bot.media import send_image_to_chat
from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    SubscriptionType,
    get_category_media_by_subscription_types,
)
from cringe_pics_telebot.services.media_delivery import deliver_category_media
from cringe_pics_telebot.services.random_image import choose_random_image
from cringe_pics_telebot.services.scheduler import aware_datetime, seconds_until_next_tick, validate_interval
from cringe_pics_telebot.services.subscriptions import get_scheduled_subscription_types, get_subscription_users

logger = logging.getLogger(__name__)

DEFAULT_CHECK_INTERVAL = timedelta(seconds=30)
_DEDUPE_TTL = timedelta(minutes=2)

type TimeProvider = Callable[[], datetime]
type Sleep = Callable[[float], Awaitable[None]]


async def run_subscription_broadcasts(
    bot: Bot,
    *,
    interval: timedelta = DEFAULT_CHECK_INTERVAL,
    now: TimeProvider | None = None,
    sleep: Sleep = asyncio.sleep,
) -> None:
    now = now or _now
    validate_interval(interval)
    while True:
        await run_due_subscription_broadcasts(bot, now=now())
        await sleep(seconds_until_next_tick(current_time=now(), interval=interval))


async def run_due_subscription_broadcasts(bot: Bot, *, now: datetime | None = None) -> int:
    current_time = now or _now()
    subscription_types = await get_scheduled_subscription_types()
    if not subscription_types:
        return 0

    sent_counts = await asyncio.gather(
        *(
            _broadcast_subscription_type(
                bot=bot,
                subscription_type=subscription_type,
                current_time=current_time,
            )
            for subscription_type in subscription_types
        )
    )

    return sum(sent_counts)


async def _broadcast_subscription_type(*, bot: Bot, subscription_type: SubscriptionType, current_time: datetime) -> int:
    scheduled_time = subscription_type.time
    if scheduled_time is None:
        return 0

    users = [
        user
        for user in await get_subscription_users(subscription_type.id)
        if _same_local_minute(
            scheduled_time,
            current_time,
            timezone_offset_minutes=user.timezone_offset_minutes,
        )
    ]
    if not users:
        return 0

    media = await get_category_media_by_subscription_types([subscription_type.id])
    if not media:
        return 0

    reservations = await asyncio.gather(
        *(
            _reserve_scheduled_send(
                subscription_type_id=subscription_type.id,
                user_id=user.id,
                current_time=current_time,
            )
            for user in users
        )
    )
    reserved_user_ids = [user.id for user, reserved in zip(users, reservations, strict=True) if reserved]

    if not reserved_user_ids:
        return 0

    sent_results = await asyncio.gather(
        *(
            _send_scheduled_image_to_user(
                bot=bot,
                user_id=user_id,
                subscription_type=subscription_type,
                media=media,
            )
            for user_id in reserved_user_ids
        )
    )

    return sum(sent_results)


async def _send_scheduled_image_to_user(
    *,
    bot: Bot,
    user_id: int,
    subscription_type: SubscriptionType,
    media: Sequence[CategoryMedia],
) -> int:
    try:
        selected_media = choose_random_image(media)
        await deliver_category_media(
            selected_media,
            send=lambda image: send_image_to_chat(bot=bot, chat_id=user_id, image=image),
        )
    except Exception:
        logger.exception(
            "Failed to send scheduled image for subscription type %d to user %d",
            subscription_type.id,
            user_id,
        )
        return 0

    return 1


async def _reserve_scheduled_send(*, subscription_type_id: int, user_id: int, current_time: datetime) -> bool:
    return await cache.set_if_absent(
        key=_dedupe_key(subscription_type_id=subscription_type_id, user_id=user_id, current_time=current_time),
        value=True,
        cls=bool,
        ttl=_DEDUPE_TTL,
    )


def _dedupe_key(*, subscription_type_id: int, user_id: int, current_time: datetime) -> str:
    minute = aware_datetime(current_time).astimezone(UTC).strftime("%Y%m%d%H%M")
    return f"subscription-broadcast:{subscription_type_id}:{user_id}:{minute}"


def _same_local_minute(
    scheduled_time: time,
    current_time: datetime,
    *,
    timezone_offset_minutes: int,
) -> bool:
    local_time = aware_datetime(current_time).astimezone(timezone(timedelta(minutes=timezone_offset_minutes)))
    return scheduled_time.hour == local_time.hour and scheduled_time.minute == local_time.minute


def _now() -> datetime:
    return datetime.now(UTC)

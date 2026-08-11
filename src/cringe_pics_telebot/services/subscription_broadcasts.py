import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

from aiogram import Bot

from cringe_pics_telebot.bot.media import get_message_media_file_id, send_image_to_chat
from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.services.random_image import get_random_image, update_image_cache
from cringe_pics_telebot.services.subscriptions import get_subscription_types, get_subscription_user_ids

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
    _validate_interval(interval)
    while True:
        await run_due_subscription_broadcasts(bot, now=now())
        await sleep(_seconds_until_next_tick(current_time=now(), interval=interval))


async def run_due_subscription_broadcasts(bot: Bot, *, now: datetime | None = None) -> int:
    current_time = now or _now()
    due_subscription_types = await _due_subscription_types(current_time)
    if not due_subscription_types:
        return 0

    sent_counts = await asyncio.gather(
        *(
            _broadcast_subscription_type(
                bot=bot,
                subscription_type=subscription_type,
                current_time=current_time,
            )
            for subscription_type in due_subscription_types
        )
    )

    return sum(sent_counts)


async def _due_subscription_types(current_time: datetime) -> list[SubscriptionType]:
    return [
        subscription_type
        for subscription_type in await get_subscription_types()
        if _same_minute(subscription_type.time, current_time)
    ]


async def _broadcast_subscription_type(*, bot: Bot, subscription_type: SubscriptionType, current_time: datetime) -> int:
    user_ids = await get_subscription_user_ids(subscription_type.id)
    if not user_ids:
        return 0

    reservations = await asyncio.gather(
        *(
            _reserve_scheduled_send(
                subscription_type_id=subscription_type.id,
                user_id=user_id,
                current_time=current_time,
            )
            for user_id in user_ids
        )
    )
    reserved_user_ids = [user_id for user_id, reserved in zip(user_ids, reservations, strict=True) if reserved]

    if not reserved_user_ids:
        return 0

    sent_results = await asyncio.gather(
        *(
            _send_scheduled_image_to_user(
                bot=bot,
                user_id=user_id,
                subscription_type=subscription_type,
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
) -> int:
    try:
        image = await get_random_image(subscription_type.id)
        message = await send_image_to_chat(bot=bot, chat_id=user_id, image=image)
    except Exception:
        logger.exception(
            "Failed to send scheduled image for subscription type %d to user %d",
            subscription_type.id,
            user_id,
        )
        return 0

    try:
        await update_image_cache(image_path=image.path, image_id=get_message_media_file_id(message))
    except Exception:
        logger.exception("Failed to update image %s in cache", image.path)

    return 1


async def _reserve_scheduled_send(*, subscription_type_id: int, user_id: int, current_time: datetime) -> bool:
    return await cache.set_if_absent(
        key=_dedupe_key(subscription_type_id=subscription_type_id, user_id=user_id, current_time=current_time),
        value=True,
        cls=bool,
        ttl=_DEDUPE_TTL,
    )


def _dedupe_key(*, subscription_type_id: int, user_id: int, current_time: datetime) -> str:
    minute = _aware_datetime(current_time).astimezone(UTC).strftime("%Y%m%d%H%M")
    return f"subscription-broadcast:{subscription_type_id}:{user_id}:{minute}"


def _same_minute(scheduled_time: time, current_time: datetime) -> bool:
    if scheduled_time.tzinfo is not None and current_time.tzinfo is not None:
        current_time = current_time.astimezone(scheduled_time.tzinfo)

    return scheduled_time.hour == current_time.hour and scheduled_time.minute == current_time.minute


def _seconds_until_next_tick(*, current_time: datetime, interval: timedelta) -> float:
    interval_seconds = interval.total_seconds()
    current_second = current_time.second + current_time.microsecond / 1_000_000
    seconds_after_boundary = current_second % interval_seconds
    if seconds_after_boundary == 0:
        return interval_seconds

    return interval_seconds - seconds_after_boundary


def _validate_interval(interval: timedelta) -> None:
    interval_seconds = interval.total_seconds()
    if interval_seconds <= 0 or interval_seconds > 60:
        raise ValueError("Subscription broadcast interval must be between 0 and 60 seconds")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from cringe_pics_telebot.repositories.postgres import (
    complete_admin_broadcast,
    deactivate_user,
    finish_admin_broadcast_delivery,
    get_admin_broadcast_users,
    get_dispatchable_admin_broadcasts,
    reserve_admin_broadcast_deliveries,
    transaction,
)
from cringe_pics_telebot.repositories.postgres.entities import (
    AdminBroadcast,
    AdminBroadcastDelivery,
    AdminBroadcastDeliveryStatus,
    User,
)
from cringe_pics_telebot.services.scheduler import aware_datetime, seconds_until_next_tick, validate_interval
from cringe_pics_telebot.services.timezones import (
    MAX_TIMEZONE_OFFSET_MINUTES,
    MIN_TIMEZONE_OFFSET_MINUTES,
)

logger = logging.getLogger(__name__)

DEFAULT_CHECK_INTERVAL = timedelta(seconds=30)
_MAX_ERROR_LENGTH = 1000

type TimeProvider = Callable[[], datetime]
type Sleep = Callable[[float], Awaitable[None]]


async def run_admin_broadcasts(
    bot: Bot,
    *,
    interval: timedelta = DEFAULT_CHECK_INTERVAL,
    now: TimeProvider | None = None,
    sleep: Sleep = asyncio.sleep,
) -> None:
    now = now or _now
    validate_interval(interval)
    while True:
        try:
            await run_due_admin_broadcasts(bot, now=now())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to process due admin broadcasts")
        await sleep(seconds_until_next_tick(current_time=now(), interval=interval))


async def run_due_admin_broadcasts(bot: Bot, *, now: datetime | None = None) -> int:
    current_time = aware_datetime(now or _now())
    broadcasts = await get_dispatchable_admin_broadcasts()
    if not broadcasts:
        return 0

    sent_counts = await asyncio.gather(
        *(_process_admin_broadcast(bot=bot, broadcast=broadcast, now=current_time) for broadcast in broadcasts)
    )
    return sum(sent_counts)


async def _process_admin_broadcast(
    *,
    bot: Bot,
    broadcast: AdminBroadcast,
    now: datetime,
) -> int:
    try:
        users = await get_admin_broadcast_users(broadcast.id)
        due_user_ids = [user.id for user in users if is_admin_broadcast_due(broadcast, user=user, now=now)]
        async with transaction():
            deliveries = await reserve_admin_broadcast_deliveries(
                broadcast_id=broadcast.id,
                user_ids=due_user_ids,
            )

        results = await asyncio.gather(
            *(
                _send_admin_broadcast_delivery(bot=bot, broadcast=broadcast, delivery=delivery)
                for delivery in deliveries
            )
        )

        if is_admin_broadcast_complete(broadcast, now=now):
            async with transaction():
                await complete_admin_broadcast(broadcast.id)

        return sum(results)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to process admin broadcast %d", broadcast.id)
        return 0


async def _send_admin_broadcast_delivery(
    *, bot: Bot, broadcast: AdminBroadcast, delivery: AdminBroadcastDelivery
) -> int:
    try:
        await bot.copy_message(
            chat_id=delivery.user_id,
            from_chat_id=broadcast.source_chat_id,
            message_id=broadcast.source_message_id,
        )
    except asyncio.CancelledError:
        raise
    except TelegramForbiddenError as error:
        logger.info("User %d is unavailable for admin broadcasts", delivery.user_id)
        try:
            async with transaction():
                await deactivate_user(delivery.user_id)
                await finish_admin_broadcast_delivery(
                    delivery.id,
                    status=AdminBroadcastDeliveryStatus.failed,
                    error=_delivery_error(error),
                )
        except Exception:
            logger.exception("Failed to record forbidden delivery %d", delivery.id)
        return 0
    except Exception as error:
        logger.exception(
            "Failed to copy admin broadcast %d to user %d",
            broadcast.id,
            delivery.user_id,
        )
        try:
            async with transaction():
                await finish_admin_broadcast_delivery(
                    delivery.id,
                    status=AdminBroadcastDeliveryStatus.failed,
                    error=_delivery_error(error),
                )
        except Exception:
            logger.exception("Failed to record failed delivery %d", delivery.id)
        return 0

    try:
        async with transaction():
            await finish_admin_broadcast_delivery(
                delivery.id,
                status=AdminBroadcastDeliveryStatus.sent,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to record successful delivery %d", delivery.id)

    return 1


def is_admin_broadcast_due(broadcast: AdminBroadcast, *, user: User, now: datetime) -> bool:
    offset_minutes = broadcast.timezone_offset_minutes
    if offset_minutes is None:
        offset_minutes = user.timezone_offset_minutes
    return _local_naive_datetime(now, offset_minutes=offset_minutes) >= broadcast.scheduled_local_at


def is_admin_broadcast_complete(broadcast: AdminBroadcast, *, now: datetime) -> bool:
    offset_minutes = broadcast.timezone_offset_minutes
    if offset_minutes is None:
        offset_minutes = MIN_TIMEZONE_OFFSET_MINUTES
    return _local_naive_datetime(now, offset_minutes=offset_minutes) >= broadcast.scheduled_local_at


def _local_naive_datetime(value: datetime, *, offset_minutes: int) -> datetime:
    if not MIN_TIMEZONE_OFFSET_MINUTES <= offset_minutes <= MAX_TIMEZONE_OFFSET_MINUTES:
        raise ValueError("Timezone offset must be between -12:00 and +14:00")
    return aware_datetime(value).astimezone(timezone(timedelta(minutes=offset_minutes))).replace(tzinfo=None)


def _delivery_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:_MAX_ERROR_LENGTH]


def _now() -> datetime:
    return datetime.now(UTC)

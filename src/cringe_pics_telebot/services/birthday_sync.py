import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import ChatFullInfo

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.user_birthday import UserBirthdaySource
from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import (
    User,
    clear_user_birthday,
    get_user_for_update,
    get_users_page,
    set_user_birthday,
    transaction,
)

logger = logging.getLogger(__name__)

DEFAULT_LEASE_TTL = timedelta(minutes=30)
BIRTHDAY_SYNC_LEASE_KEY = "birthday-sync:telegram-profiles"
DEFAULT_PAGE_SIZE = 100
DEFAULT_WORKER_COUNT = 5

type Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BirthdaySyncSummary:
    acquired: bool
    completed: bool = False
    considered: int = 0
    telegram_birthdates: int = 0
    created: int = 0
    updated: int = 0
    cleared: int = 0
    unchanged: int = 0
    manual_skipped: int = 0
    missing: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class _BirthdaySyncDelta:
    telegram_birthdates: int = 0
    created: int = 0
    updated: int = 0
    cleared: int = 0
    unchanged: int = 0
    manual_skipped: int = 0
    missing: int = 0
    failed: int = 0

    def __add__(self, other: _BirthdaySyncDelta) -> _BirthdaySyncDelta:
        return _BirthdaySyncDelta(
            telegram_birthdates=self.telegram_birthdates + other.telegram_birthdates,
            created=self.created + other.created,
            updated=self.updated + other.updated,
            cleared=self.cleared + other.cleared,
            unchanged=self.unchanged + other.unchanged,
            manual_skipped=self.manual_skipped + other.manual_skipped,
            missing=self.missing + other.missing,
            failed=self.failed + other.failed,
        )


async def synchronize_telegram_birthdays(
    bot: Bot,
    *,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
    page_size: int = DEFAULT_PAGE_SIZE,
    worker_count: int = DEFAULT_WORKER_COUNT,
    sleep: Sleep = asyncio.sleep,
) -> BirthdaySyncSummary:
    _validate_positive_duration(lease_ttl)
    _validate_positive_integer(page_size, "page_size")
    _validate_positive_integer(worker_count, "worker_count")

    lease_token = secrets.token_urlsafe(24)
    acquired = await cache.set_if_absent(
        key=BIRTHDAY_SYNC_LEASE_KEY,
        value=lease_token,
        cls=str,
        ttl=lease_ttl,
    )
    if not acquired:
        logger.info("Skipped Telegram birthday sync because another instance owns the lease")
        return BirthdaySyncSummary(acquired=False)

    started_at = monotonic()
    considered = 0
    delta = _BirthdaySyncDelta()
    completed = True
    after_user_id = 0
    try:
        while True:
            users = await get_users_page(after_user_id=after_user_id, limit=page_size)
            if not users:
                break

            considered += len(users)
            delta += await _synchronize_page(bot, users, worker_count=worker_count, sleep=sleep)
            after_user_id = users[-1].id
            if len(users) < page_size:
                break

            refreshed = await cache.refresh_if_value(
                key=BIRTHDAY_SYNC_LEASE_KEY,
                value=lease_token,
                cls=str,
                ttl=lease_ttl,
            )
            if not refreshed:
                logger.warning("Stopped Telegram birthday sync after losing the lease")
                completed = False
                break

        result = BirthdaySyncSummary(
            acquired=True,
            completed=completed,
            considered=considered,
            telegram_birthdates=delta.telegram_birthdates,
            created=delta.created,
            updated=delta.updated,
            cleared=delta.cleared,
            unchanged=delta.unchanged,
            manual_skipped=delta.manual_skipped,
            missing=delta.missing,
            failed=delta.failed,
        )
        logger.info(
            "Finished Telegram birthday sync duration_seconds=%.3f completed=%s considered=%d "
            "telegram_birthdates=%d created=%d updated=%d cleared=%d unchanged=%d manual_skipped=%d "
            "missing=%d failed=%d",
            monotonic() - started_at,
            result.completed,
            result.considered,
            result.telegram_birthdates,
            result.created,
            result.updated,
            result.cleared,
            result.unchanged,
            result.manual_skipped,
            result.missing,
            result.failed,
        )
        return result
    finally:
        try:
            await cache.delete_if_value(key=BIRTHDAY_SYNC_LEASE_KEY, value=lease_token, cls=str)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to release Telegram birthday sync lease")


async def _synchronize_page(
    bot: Bot,
    users: list[User],
    *,
    worker_count: int,
    sleep: Sleep,
) -> _BirthdaySyncDelta:
    queue: asyncio.Queue[User] = asyncio.Queue()
    for user in users:
        queue.put_nowait(user)

    workers = [
        asyncio.create_task(_birthday_worker(bot, queue, sleep=sleep)) for _ in range(min(worker_count, len(users)))
    ]
    worker_results = await asyncio.gather(*workers)

    result = _BirthdaySyncDelta()
    for worker_result in worker_results:
        result += worker_result
    return result


async def _birthday_worker(bot: Bot, queue: asyncio.Queue[User], *, sleep: Sleep) -> _BirthdaySyncDelta:
    result = _BirthdaySyncDelta()
    while True:
        try:
            user = queue.get_nowait()
        except asyncio.QueueEmpty:
            return result

        result += await _synchronize_user(bot, user, sleep=sleep)


async def _synchronize_user(bot: Bot, user: User, *, sleep: Sleep) -> _BirthdaySyncDelta:
    if user.birthday_source is UserBirthdaySource.manual:
        return _BirthdaySyncDelta(manual_skipped=1)

    try:
        chat = await _get_chat_with_retry(bot, user.id, sleep=sleep)
        birthday = _birthday_from_chat(chat, expected_user_id=user.id)
        result = await _apply_telegram_birthday(user.id, birthday)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "Failed to synchronize Telegram birthday user_id=%d error_type=%s",
            user.id,
            type(error).__name__,
        )
        return _BirthdaySyncDelta(failed=1)

    if birthday is None:
        return _BirthdaySyncDelta(missing=1) + result
    return _BirthdaySyncDelta(telegram_birthdates=1) + result


async def _get_chat_with_retry(bot: Bot, user_id: int, *, sleep: Sleep) -> ChatFullInfo:
    try:
        return await bot.get_chat(user_id)
    except TelegramRetryAfter as error:
        await sleep(error.retry_after)
        return await bot.get_chat(user_id)


def _birthday_from_chat(chat: ChatFullInfo, *, expected_user_id: int) -> AnnualDate | None:
    if chat.id != expected_user_id or chat.type != ChatType.PRIVATE:
        raise InvalidTelegramProfileError
    if chat.birthdate is None:
        return None
    return AnnualDate(month=chat.birthdate.month, day=chat.birthdate.day)


async def _apply_telegram_birthday(user_id: int, birthday: AnnualDate | None) -> _BirthdaySyncDelta:
    async with transaction():
        user = await get_user_for_update(user_id)
        if user is None:
            return _BirthdaySyncDelta(failed=1)
        if user.birthday_source is UserBirthdaySource.manual:
            return _BirthdaySyncDelta(manual_skipped=1)

        if birthday is None:
            if user.birthday_source is UserBirthdaySource.telegram:
                await clear_user_birthday(user_id)
                return _BirthdaySyncDelta(cleared=1)
            return _BirthdaySyncDelta(unchanged=1)

        if user.birthday == birthday:
            return _BirthdaySyncDelta(unchanged=1)

        await set_user_birthday(
            user_id=user_id,
            birthday=birthday,
            source=UserBirthdaySource.telegram,
        )
        if user.birthday is None:
            return _BirthdaySyncDelta(created=1)
        return _BirthdaySyncDelta(updated=1)


def _validate_positive_duration(value: timedelta) -> None:
    if value.total_seconds() <= 0:
        raise ValueError("Birthday sync lease TTL must be positive")


def _validate_positive_integer(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"Birthday sync {name} must be positive")


class InvalidTelegramProfileError(ValueError): ...

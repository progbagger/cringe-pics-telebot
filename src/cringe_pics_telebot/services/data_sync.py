import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot

from cringe_pics_telebot.services.birthday_sync import BirthdaySyncSummary, synchronize_telegram_birthdays
from cringe_pics_telebot.services.media_alias_enrichment_settings import MediaAliasEnrichmentSettings
from cringe_pics_telebot.services.media_sync import DEFAULT_SYNC_INTERVAL, MediaSyncSummary, synchronize_media_catalog

logger = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DataSyncSummary:
    media: MediaSyncSummary | None
    profiles: BirthdaySyncSummary | None


async def synchronize_bot_data(
    bot: Bot,
    *,
    alias_enrichment_settings: MediaAliasEnrichmentSettings | None = None,
) -> DataSyncSummary:
    media: MediaSyncSummary | None = None
    profiles: BirthdaySyncSummary | None = None
    try:
        media = await synchronize_media_catalog(alias_enrichment_settings=alias_enrichment_settings)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to synchronize media catalog stage")

    try:
        profiles = await synchronize_telegram_birthdays(bot)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to synchronize Telegram birthday stage")

    return DataSyncSummary(media=media, profiles=profiles)


async def run_data_sync(
    bot: Bot,
    *,
    interval: timedelta = DEFAULT_SYNC_INTERVAL,
    sleep: Sleep = asyncio.sleep,
    alias_enrichment_settings: MediaAliasEnrichmentSettings | None = None,
) -> None:
    if interval.total_seconds() <= 0:
        raise ValueError("Data sync interval must be positive")

    while True:
        await synchronize_bot_data(bot, alias_enrichment_settings=alias_enrichment_settings)
        await sleep(interval.total_seconds())

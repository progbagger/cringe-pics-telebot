import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import timedelta
from time import monotonic

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from cringe_pics_telebot.bot.media import get_message_media_file_ids
from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    get_category_media,
    invalidate_category_media_file_id,
    materialize_category_media,
    transaction,
)
from cringe_pics_telebot.repositories.yandex import get_download_urls
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia

logger = logging.getLogger(__name__)

MATERIALIZATION_LEASE_TTL = timedelta(seconds=30)
MATERIALIZATION_WAIT_TIMEOUT = timedelta(seconds=30)
MATERIALIZATION_POLL_INTERVAL = 0.1

type MediaSender = Callable[[LinkedMedia | CachedMedia], Awaitable[Message]]
type Sleep = Callable[[float], Awaitable[None]]
type Clock = Callable[[], float]


async def deliver_category_media(
    media: CategoryMedia,
    *,
    send: MediaSender,
    lease_ttl: timedelta = MATERIALIZATION_LEASE_TTL,
    wait_timeout: timedelta = MATERIALIZATION_WAIT_TIMEOUT,
    poll_interval: float = MATERIALIZATION_POLL_INTERVAL,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = monotonic,
) -> Message:
    if lease_ttl.total_seconds() <= 0 or wait_timeout.total_seconds() <= 0 or poll_interval <= 0:
        raise ValueError("Media materialization timings must be positive")

    current = media
    recovered_invalid_file_id = False
    deadline = clock() + wait_timeout.total_seconds()
    while True:
        _ensure_same_active_revision(current, expected=media)
        if current.telegram_file_id is not None:
            try:
                return await send(_cached_media(current))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if recovered_invalid_file_id or not _is_invalid_file_id_error(error):
                    raise
                async with transaction():
                    invalidated = await invalidate_category_media_file_id(
                        media_id=current.id,
                        telegram_file_id=current.telegram_file_id,
                    )
                current = invalidated or await _get_current_media(current.id)
                recovered_invalid_file_id = True
                continue

        lease_token = secrets.token_urlsafe(24)
        acquired = await cache.set_if_absent(
            key=_materialization_lease_key(current),
            value=lease_token,
            cls=str,
            ttl=lease_ttl,
        )
        if acquired:
            return await _materialize_as_lease_owner(current, send=send, lease_token=lease_token)

        remaining = deadline - clock()
        if remaining <= 0:
            raise MediaMaterializationTimeout(current.id)
        await sleep(min(poll_interval, remaining))
        current = await _get_current_media(current.id)


async def _materialize_as_lease_owner(
    media: CategoryMedia,
    *,
    send: MediaSender,
    lease_token: str,
) -> Message:
    lease_key = _materialization_lease_key(media)
    try:
        download_url, *_ = await get_download_urls([media.source_path])
        if download_url is None:
            raise MediaDownloadUrlError(media.source_path)
        message = await send(_linked_media(media, download_url))
        telegram_file_id, telegram_file_unique_id = get_message_media_file_ids(message)
        async with transaction():
            await materialize_category_media(
                media_id=media.id,
                source_revision=media.source_revision,
                telegram_file_id=telegram_file_id,
                telegram_file_unique_id=telegram_file_unique_id,
            )
        return message
    finally:
        try:
            await cache.delete_if_value(key=lease_key, value=lease_token, cls=str)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to release media materialization lease for media %d", media.id)


async def _get_current_media(media_id: int) -> CategoryMedia:
    current = await get_category_media(media_id)
    if current is None:
        raise MediaUnavailableError(media_id)
    return current


def _ensure_same_active_revision(current: CategoryMedia, *, expected: CategoryMedia) -> None:
    if not current.is_active or current.source_revision != expected.source_revision:
        raise MediaUnavailableError(current.id)


def _materialization_lease_key(media: CategoryMedia) -> str:
    return f"media-materialization:{media.id}:{media.source_revision}"


def _cached_media(media: CategoryMedia) -> CachedMedia:
    assert media.telegram_file_id is not None
    return CachedMedia(
        name=media.name,
        mime_type=media.mime_type,
        path=media.source_path,
        source_revision=media.source_revision,
        id=media.telegram_file_id,
    )


def _linked_media(media: CategoryMedia, url: str) -> LinkedMedia:
    return LinkedMedia(
        name=media.name,
        mime_type=media.mime_type,
        path=media.source_path,
        source_revision=media.source_revision,
        url=url,
    )


def _is_invalid_file_id_error(error: Exception) -> bool:
    if not isinstance(error, TelegramBadRequest):
        return False
    message = error.message.casefold()
    return "wrong file identifier" in message or "wrong remote file identifier" in message


class MediaDeliveryError(RuntimeError): ...


class MediaUnavailableError(MediaDeliveryError): ...


class MediaDownloadUrlError(MediaDeliveryError): ...


class MediaMaterializationTimeout(MediaDeliveryError): ...

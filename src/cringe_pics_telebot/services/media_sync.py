import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaReconcileResult,
    CategoryMediaSource,
    SubscriptionType,
    TelegramMediaType,
    get_all_subscription_types,
)
from cringe_pics_telebot.repositories.yandex import Image, list_dir
from cringe_pics_telebot.services.media_catalog import reconcile_category_media_snapshot

logger = logging.getLogger(__name__)

DEFAULT_SYNC_INTERVAL = timedelta(hours=12)
DEFAULT_LEASE_TTL = timedelta(minutes=30)
MEDIA_SYNC_LEASE_KEY = "media-sync:full-catalog"
MAX_TELEGRAM_VIDEO_URL_SIZE_BYTES = 20 * 1024 * 1024

type Sleep = Callable[[float], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class MediaSyncSummary:
    acquired: bool
    categories: int = 0
    failed: int = 0
    discovered: int = 0
    created: int = 0
    changed: int = 0
    reactivated: int = 0
    deactivated: int = 0


async def run_media_sync(
    *,
    interval: timedelta = DEFAULT_SYNC_INTERVAL,
    sleep: Sleep = asyncio.sleep,
) -> None:
    _validate_interval(interval)
    while True:
        try:
            await synchronize_media_catalog()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to synchronize media catalog")
        await sleep(interval.total_seconds())


async def synchronize_media_catalog(*, lease_ttl: timedelta = DEFAULT_LEASE_TTL) -> MediaSyncSummary:
    _validate_interval(lease_ttl)
    lease_token = secrets.token_urlsafe(24)
    acquired = await cache.set_if_absent(
        key=MEDIA_SYNC_LEASE_KEY,
        value=lease_token,
        cls=str,
        ttl=lease_ttl,
    )
    if not acquired:
        logger.info("Skipped media catalog sync because another instance owns the lease")
        return MediaSyncSummary(acquired=False)

    started_at = monotonic()
    try:
        subscription_types = await get_all_subscription_types()
        summaries: list[CategoryMediaReconcileResult] = []
        failed = 0
        for subscription_type in subscription_types:
            try:
                summary = await _synchronize_subscription_type(
                    subscription_type,
                    lease_token=lease_token,
                    lease_ttl=lease_ttl,
                )
            except asyncio.CancelledError:
                raise
            except MediaSyncLeaseLost:
                logger.warning("Stopped media catalog sync after losing the lease")
                failed += 1
                break
            except Exception:
                logger.exception(
                    "Failed to synchronize media category id=%d name=%s",
                    subscription_type.id,
                    subscription_type.name,
                )
                failed += 1
            else:
                summaries.append(summary)
                logger.info(
                    "Synchronized media category id=%d name=%s discovered=%d created=%d changed=%d "
                    "reactivated=%d deactivated=%d",
                    subscription_type.id,
                    subscription_type.name,
                    summary.discovered,
                    summary.created,
                    summary.changed,
                    summary.reactivated,
                    summary.deactivated,
                )

        result = MediaSyncSummary(
            acquired=True,
            categories=len(summaries),
            failed=failed,
            discovered=sum(summary.discovered for summary in summaries),
            created=sum(summary.created for summary in summaries),
            changed=sum(summary.changed for summary in summaries),
            reactivated=sum(summary.reactivated for summary in summaries),
            deactivated=sum(summary.deactivated for summary in summaries),
        )
        logger.info(
            "Finished media catalog sync duration_seconds=%.3f categories=%d failed=%d discovered=%d "
            "created=%d changed=%d reactivated=%d deactivated=%d",
            monotonic() - started_at,
            result.categories,
            result.failed,
            result.discovered,
            result.created,
            result.changed,
            result.reactivated,
            result.deactivated,
        )
        return result
    finally:
        try:
            await cache.delete_if_value(key=MEDIA_SYNC_LEASE_KEY, value=lease_token, cls=str)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to release media catalog sync lease")


async def _synchronize_subscription_type(
    subscription_type: SubscriptionType,
    *,
    lease_token: str,
    lease_ttl: timedelta,
) -> CategoryMediaReconcileResult:
    images = [image async for image in list_dir(subscription_type.s3_directory_path)]
    if not await cache.refresh_if_value(
        key=MEDIA_SYNC_LEASE_KEY,
        value=lease_token,
        cls=str,
        ttl=lease_ttl,
    ):
        raise MediaSyncLeaseLost
    sources = [source for image in images if (source := _category_media_source(image)) is not None]
    return await reconcile_category_media_snapshot(
        subscription_type_id=subscription_type.id,
        sources=sources,
    )


def _category_media_source(image: Image) -> CategoryMediaSource | None:
    if image.mime_type == "image/gif":
        media_type = TelegramMediaType.animation
    elif image.mime_type.startswith("image/"):
        media_type = TelegramMediaType.photo
    elif image.mime_type == "video/mp4":
        if image.size is None:
            logger.warning("Skipped MP4 video with unknown size path=%s", image.path)
            return None
        if image.size > MAX_TELEGRAM_VIDEO_URL_SIZE_BYTES:
            logger.warning(
                "Skipped MP4 video exceeding Telegram HTTP URL limit path=%s size=%d limit=%d",
                image.path,
                image.size,
                MAX_TELEGRAM_VIDEO_URL_SIZE_BYTES,
            )
            return None
        media_type = TelegramMediaType.video
    else:
        logger.warning("Skipped unsupported media path=%s mime_type=%s", image.path, image.mime_type)
        return None

    return CategoryMediaSource(
        source_path=image.path,
        source_revision=image.source_revision,
        name=image.name,
        mime_type=image.mime_type,
        telegram_media_type=media_type,
    )


def _validate_interval(interval: timedelta) -> None:
    if interval.total_seconds() <= 0:
        raise ValueError("Media sync interval must be positive")


class MediaSyncLeaseLost(RuntimeError): ...

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from cringe_pics_telebot.helpers.metrics import CounterMetric, get_metrics_sink
from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaReconcileResult,
    CategoryMediaSource,
    SubscriptionType,
    TelegramMediaType,
    get_all_subscription_types,
)
from cringe_pics_telebot.repositories.yandex import Image, list_dir
from cringe_pics_telebot.services.media_alias_enrichment_settings import (
    MediaAliasEnrichmentSettings,
    load_media_alias_enrichment_settings,
)
from cringe_pics_telebot.services.media_catalog import reconcile_category_media_snapshot
from cringe_pics_telebot.services.media_deduplication import (
    MediaDeduplicationResult,
    deduplicate_category_media,
)

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
    alias_enrichment_queued: int = 0
    duplicate_groups: int = 0
    duplicates_moved: int = 0
    duplicate_move_failed: int = 0


@dataclass(slots=True, frozen=True)
class _CategoryMediaSyncResult:
    catalog: CategoryMediaReconcileResult
    deduplication: MediaDeduplicationResult


async def run_media_sync(
    *,
    interval: timedelta = DEFAULT_SYNC_INTERVAL,
    sleep: Sleep = asyncio.sleep,
    alias_enrichment_settings: MediaAliasEnrichmentSettings | None = None,
) -> None:
    _validate_interval(interval)
    while True:
        try:
            await synchronize_media_catalog(alias_enrichment_settings=alias_enrichment_settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to synchronize media catalog")
        await sleep(interval.total_seconds())


async def synchronize_media_catalog(
    *,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
    alias_enrichment_settings: MediaAliasEnrichmentSettings | None = None,
    lease_sleep: Sleep = asyncio.sleep,
) -> MediaSyncSummary:
    _validate_interval(lease_ttl)
    if alias_enrichment_settings is None:
        alias_enrichment_settings = await load_media_alias_enrichment_settings()

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

    lease_lost = asyncio.Event()

    async def ensure_lease() -> None:
        await _ensure_media_sync_lease(
            lease_token=lease_token,
            lease_ttl=lease_ttl,
            lease_lost=lease_lost,
        )

    lease_heartbeat = asyncio.create_task(
        _maintain_media_sync_lease(
            ensure_lease=ensure_lease,
            lease_ttl=lease_ttl,
            lease_sleep=lease_sleep,
        )
    )
    started_at = monotonic()
    try:
        subscription_types = await get_all_subscription_types()
        summaries: list[_CategoryMediaSyncResult] = []
        failed = 0
        for subscription_type in subscription_types:
            try:
                summary = await _synchronize_subscription_type(
                    subscription_type,
                    ensure_lease=ensure_lease,
                    alias_enrichment_settings=alias_enrichment_settings,
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
                    "reactivated=%d deactivated=%d alias_enrichment_queued=%d duplicate_groups=%d "
                    "duplicates_moved=%d duplicate_move_failed=%d",
                    subscription_type.id,
                    subscription_type.name,
                    summary.catalog.discovered,
                    summary.catalog.created,
                    summary.catalog.changed,
                    summary.catalog.reactivated,
                    summary.catalog.deactivated,
                    summary.catalog.alias_enrichment_queued,
                    summary.deduplication.groups,
                    summary.deduplication.moved,
                    summary.deduplication.move_failed,
                )
                if summary.catalog.alias_enrichment_queued:
                    get_metrics_sink().emit(
                        [CounterMetric("media_alias_enrichment.queued", summary.catalog.alias_enrichment_queued)]
                    )
                get_metrics_sink().emit(
                    [
                        CounterMetric("media_deduplication.groups", summary.deduplication.groups),
                        CounterMetric("media_deduplication.moved", summary.deduplication.moved),
                        CounterMetric("media_deduplication.move_failed", summary.deduplication.move_failed),
                    ]
                )

        result = MediaSyncSummary(
            acquired=True,
            categories=len(summaries),
            failed=failed,
            discovered=sum(summary.catalog.discovered for summary in summaries),
            created=sum(summary.catalog.created for summary in summaries),
            changed=sum(summary.catalog.changed for summary in summaries),
            reactivated=sum(summary.catalog.reactivated for summary in summaries),
            deactivated=sum(summary.catalog.deactivated for summary in summaries),
            alias_enrichment_queued=sum(summary.catalog.alias_enrichment_queued for summary in summaries),
            duplicate_groups=sum(summary.deduplication.groups for summary in summaries),
            duplicates_moved=sum(summary.deduplication.moved for summary in summaries),
            duplicate_move_failed=sum(summary.deduplication.move_failed for summary in summaries),
        )
        logger.info(
            "Finished media catalog sync duration_seconds=%.3f categories=%d failed=%d discovered=%d "
            "created=%d changed=%d reactivated=%d deactivated=%d alias_enrichment_queued=%d "
            "duplicate_groups=%d duplicates_moved=%d duplicate_move_failed=%d",
            monotonic() - started_at,
            result.categories,
            result.failed,
            result.discovered,
            result.created,
            result.changed,
            result.reactivated,
            result.deactivated,
            result.alias_enrichment_queued,
            result.duplicate_groups,
            result.duplicates_moved,
            result.duplicate_move_failed,
        )
        return result
    finally:
        lease_heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await lease_heartbeat

        try:
            await cache.delete_if_value(key=MEDIA_SYNC_LEASE_KEY, value=lease_token, cls=str)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to release media catalog sync lease")


async def _synchronize_subscription_type(
    subscription_type: SubscriptionType,
    *,
    ensure_lease: Callable[[], Awaitable[None]],
    alias_enrichment_settings: MediaAliasEnrichmentSettings,
) -> _CategoryMediaSyncResult:
    images = [image async for image in list_dir(subscription_type.s3_directory_path)]
    await ensure_lease()

    deduplication = await deduplicate_category_media(
        category_directory=subscription_type.s3_directory_path,
        images=[image for image in images if _is_deduplication_candidate(image)],
        ensure_lease=ensure_lease,
    )
    await ensure_lease()

    sources = [source for image in deduplication.images if (source := _category_media_source(image)) is not None]
    catalog = await reconcile_category_media_snapshot(
        subscription_type_id=subscription_type.id,
        sources=sources,
        alias_enrichment_settings=alias_enrichment_settings,
    )
    return _CategoryMediaSyncResult(catalog=catalog, deduplication=deduplication)


def _is_deduplication_candidate(image: Image) -> bool:
    if image.mime_type.startswith("image/"):
        return True
    return image.mime_type == "video/mp4" and image.size is not None and image.size <= MAX_TELEGRAM_VIDEO_URL_SIZE_BYTES


async def _ensure_media_sync_lease(
    *,
    lease_token: str,
    lease_ttl: timedelta,
    lease_lost: asyncio.Event,
) -> None:
    if lease_lost.is_set():
        raise MediaSyncLeaseLost

    try:
        refreshed = await cache.refresh_if_value(
            key=MEDIA_SYNC_LEASE_KEY,
            value=lease_token,
            cls=str,
            ttl=lease_ttl,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        lease_lost.set()
        raise MediaSyncLeaseLost from error

    if not refreshed:
        lease_lost.set()
        raise MediaSyncLeaseLost


async def _maintain_media_sync_lease(
    *,
    ensure_lease: Callable[[], Awaitable[None]],
    lease_ttl: timedelta,
    lease_sleep: Sleep,
) -> None:
    refresh_interval = lease_ttl.total_seconds() / 3
    while True:
        await lease_sleep(refresh_interval)
        try:
            await ensure_lease()
        except MediaSyncLeaseLost:
            logger.warning("Media catalog sync lease heartbeat stopped after losing the lease")
            return


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

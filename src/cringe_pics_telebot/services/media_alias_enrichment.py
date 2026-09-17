import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import aiohttp

from cringe_pics_telebot.entities.generated_aliases import MAX_GENERATED_ALIAS_LENGTH, MAX_GENERATED_ALIASES
from cringe_pics_telebot.entities.search_aliases import SearchAlias
from cringe_pics_telebot.helpers.metrics import (
    CounterMetric,
    GaugeMetric,
    Metric,
    Stopwatch,
    get_metrics_sink,
)
from cringe_pics_telebot.repositories.ollama import (
    InvalidOllamaResponseError,
    OllamaClient,
    OllamaHTTPError,
    OllamaTransportError,
)
from cringe_pics_telebot.repositories.postgres import (
    get_category_media,
    get_category_media_search_metadata,
    replace_media_search_aliases,
    transaction,
)
from cringe_pics_telebot.repositories.postgres.entities.media_alias_enrichment import (
    MediaAliasEnrichmentJob,
    MediaAliasEnrichmentJobStatus,
)
from cringe_pics_telebot.repositories.postgres.media_alias_enrichment import (
    claim_media_alias_enrichment_jobs,
    enqueue_media_alias_enrichment_jobs,
    finish_media_alias_enrichment_job,
    get_media_alias_enrichment_job,
    get_media_alias_enrichment_queue_counts,
    refresh_media_alias_enrichment_job_lease,
)
from cringe_pics_telebot.repositories.yandex import download_file
from cringe_pics_telebot.repositories.yandex.yandex import YandexDownloadTooLargeError
from cringe_pics_telebot.services.media_alias_enrichment_settings import MediaAliasEnrichmentSettings
from cringe_pics_telebot.services.media_alias_images import (
    MediaAliasDecodeError,
    MediaAliasFrameTooLargeError,
    MediaAliasImagePreparationError,
    PreparedMediaAliasImageTooLargeError,
    UnsupportedMediaAliasTypeError,
    prepare_media_alias_image,
)
from cringe_pics_telebot.services.search_aliases import normalize_search_term

logger = logging.getLogger(__name__)
type UtcClock = Callable[[], datetime]
type Sleep = Callable[[float], Awaitable[None]]
type JobTask = asyncio.Task[str] | asyncio.Task[None]


class _EnrichmentAttemptsExhaustedError(ValueError): ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def parse_generated_media_aliases(values: Sequence[str]) -> tuple[SearchAlias, ...]:
    if not 1 <= len(values) <= MAX_GENERATED_ALIASES:
        raise InvalidOllamaResponseError("Generated aliases exceed count limits")

    aliases: list[SearchAlias] = []
    normalized_values: set[str] = set()
    for value in values:
        if not 1 <= len(value) <= MAX_GENERATED_ALIAS_LENGTH or "\0" in value:
            raise InvalidOllamaResponseError("Generated alias is not a valid bounded string")

        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidOllamaResponseError("Generated alias is not valid Unicode") from error

        text = value.strip()
        normalized = normalize_search_term(text)
        if normalized and normalized not in normalized_values:
            aliases.append(SearchAlias(text=text, normalized=normalized))
            normalized_values.add(normalized)

    if not aliases:
        raise InvalidOllamaResponseError("Generated aliases are empty after normalization")

    return tuple(aliases)


def media_alias_retry_delay(*, retry_count: int, base: timedelta, maximum: timedelta) -> timedelta:
    delay = base
    for _ in range(1, retry_count):
        if delay >= maximum:
            return maximum
        delay = min(delay * 2, maximum)

    return delay


async def process_media_alias_enrichment_jobs(
    *,
    ollama: OllamaClient,
    settings: MediaAliasEnrichmentSettings,
    now: UtcClock = _utc_now,
    sleep: Sleep = asyncio.sleep,
) -> int:
    if not settings.enabled:
        return 0

    assert settings.ollama_model is not None and settings.prompt_sha256 is not None
    async with transaction():
        jobs = await claim_media_alias_enrichment_jobs(
            limit=settings.concurrency,
            lease_token=uuid4().hex,
            lease_ttl=settings.lease_ttl,
            model=settings.ollama_model,
            prompt_sha256=settings.prompt_sha256,
            claimed_at=now(),
        )

    _emit(CounterMetric("media_alias_enrichment.claimed", len(jobs)))
    tasks = [
        asyncio.create_task(_process_job(job=job, ollama=ollama, settings=settings, now=now, sleep=sleep))
        for job in jobs
    ]

    try:
        await asyncio.gather(*tasks)
    finally:
        if any(not task.done() for task in tasks):
            await _wait_for_cleanup(_cancel_tasks(tasks))

    async with transaction():
        counts = await get_media_alias_enrichment_queue_counts(now=now())

    _emit(
        GaugeMetric("media_alias_enrichment.queue.available", counts.available),
        GaugeMetric("media_alias_enrichment.queue.expired_processing", counts.expired_processing),
        GaugeMetric("media_alias_enrichment.queue.failed", counts.failed),
    )

    return len(jobs)


async def run_media_alias_enrichment(
    *,
    ollama: OllamaClient,
    settings: MediaAliasEnrichmentSettings,
    now: UtcClock = _utc_now,
    sleep: Sleep = asyncio.sleep,
) -> None:
    if not settings.enabled:
        return

    while True:
        try:
            claimed = await process_media_alias_enrichment_jobs(ollama=ollama, settings=settings, now=now, sleep=sleep)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("Media alias enrichment pass failed exception_type=%s", type(error).__name__)
            claimed = 0

        # Drain available jobs without making them wait an extra poll interval.
        if claimed < settings.concurrency:
            await sleep(settings.poll_interval.total_seconds())


async def _process_job(
    *,
    job: MediaAliasEnrichmentJob,
    ollama: OllamaClient,
    settings: MediaAliasEnrichmentSettings,
    now: UtcClock,
    sleep: Sleep,
) -> None:
    stopwatch = Stopwatch.start()
    work = asyncio.create_task(_enrich_job(job=job, ollama=ollama, settings=settings, now=now))
    heartbeat = asyncio.create_task(_heartbeat(job=job, settings=settings, now=now, sleep=sleep))

    try:
        try:
            done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                result_class = await work
                _record_result(job=job, result_class=result_class, stopwatch=stopwatch)
            else:
                await heartbeat
                await _cancel_tasks((work,))
                _record_result(job=job, result_class="lease_lost", stopwatch=stopwatch)
        except Exception as error:
            await _cancel_tasks((work,))

            try:
                result_class = await _handle_failure(job=job, error=error, settings=settings, now=now)
                _record_result(job=job, result_class=result_class, stopwatch=stopwatch)
            except Exception as completion_error:
                # A database outage leaves the reservation recoverable by lease expiry.
                logger.error(
                    "Media alias job completion failed job_id=%s media_id=%s exception_type=%s",
                    job.id,
                    job.media_id,
                    type(completion_error).__name__,
                )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat

            # Suppress the heartbeat's cancellation, not cancellation of this job.
            current_task = asyncio.current_task()
            assert current_task is not None
            if current_task.cancelling():
                raise asyncio.CancelledError
    except asyncio.CancelledError:
        result_class = await _wait_for_cleanup(_cancel_and_release(job=job, tasks=(work, heartbeat), now=now))
        if work.done() and not work.cancelled() and work.exception() is None:
            result_class = work.result()
        _record_result(job=job, result_class=result_class, stopwatch=stopwatch)
        raise


async def _enrich_job(
    *,
    job: MediaAliasEnrichmentJob,
    ollama: OllamaClient,
    settings: MediaAliasEnrichmentSettings,
    now: UtcClock,
) -> str:
    async with transaction():
        metadata = await get_category_media_search_metadata(job.media_id)
        current_job = await get_media_alias_enrichment_job(job.id)
        if not _owns_job(current_job=current_job, claim=job, now=now()):
            return "lease_lost"

    if metadata is None:
        return "lease_lost"
    if not metadata.media.is_active or metadata.media.source_revision != job.source_revision or metadata.search_aliases:
        return await _complete_job(job=job, aliases=(), settings=settings, now=now)
    if job.retry_count > settings.max_attempts:
        raise _EnrichmentAttemptsExhaustedError("Media alias enrichment retry budget is exhausted")

    with Stopwatch.start(metric_name="media_alias_enrichment.media_prepare"):
        downloaded = await download_file(
            path=metadata.media.source_path,
            max_bytes=settings.max_source_bytes,
            timeout=settings.download_timeout,
        )
        image = await prepare_media_alias_image(
            source=downloaded.content,
            mime_type=metadata.media.mime_type,
            max_frame_pixels=settings.max_frame_pixels,
            max_image_edge_pixels=settings.max_image_edge_pixels,
            max_image_bytes=settings.max_image_bytes,
        )

    assert settings.ollama_model is not None and settings.prompt is not None
    with Stopwatch.start(metric_name="media_alias_enrichment.ollama_request"):
        values = await ollama.generate_aliases(image=image, model=settings.ollama_model, prompt=settings.prompt)

    aliases = parse_generated_media_aliases(values)
    return await _complete_job(job=job, aliases=aliases, settings=settings, now=now)


async def _complete_job(
    *,
    job: MediaAliasEnrichmentJob,
    aliases: Sequence[SearchAlias],
    settings: MediaAliasEnrichmentSettings,
    now: UtcClock,
) -> str:
    assert job.lease_token is not None
    queued = 0

    async with transaction():
        # Reconcile and manual editing use the same media -> job lock order.
        media = await get_category_media(job.media_id, with_for_update=True)
        current_job = await get_media_alias_enrichment_job(job.id, with_for_update=True)
        timestamp = now()
        if media is None or not _owns_job(current_job=current_job, claim=job, now=timestamp):
            return "lease_lost"

        metadata = await get_category_media_search_metadata(job.media_id)
        assert metadata is not None

        if not media.is_active:
            result_class = "inactive"
        elif media.source_revision != job.source_revision:
            result_class = "stale_revision"
        elif metadata.search_aliases:
            result_class = "manual_aliases_won"
        else:
            result_class = "succeeded"

        status = (
            MediaAliasEnrichmentJobStatus.succeeded
            if result_class == "succeeded"
            else MediaAliasEnrichmentJobStatus.obsolete
        )

        owned = await finish_media_alias_enrichment_job(
            job_id=job.id,
            lease_token=job.lease_token,
            status=status,
            result_class=result_class,
            finished_at=timestamp,
        )
        if not owned:
            return "lease_lost"

        if result_class == "succeeded":
            # Only the locked, still-aliasless current media may receive aliases.
            if not aliases:
                raise InvalidOllamaResponseError("Generated aliases are empty")
            await replace_media_search_aliases(media.id, aliases)
        elif result_class == "stale_revision":
            assert settings.ollama_model is not None and settings.prompt_sha256 is not None
            queued = await enqueue_media_alias_enrichment_jobs(
                subscription_type_id=media.subscription_type_id,
                model=settings.ollama_model,
                prompt_sha256=settings.prompt_sha256,
                enqueued_at=timestamp,
                media_ids=(media.id,),
            )

    if queued:
        _emit(CounterMetric("media_alias_enrichment.queued", queued))

    return result_class


async def _heartbeat(
    *,
    job: MediaAliasEnrichmentJob,
    settings: MediaAliasEnrichmentSettings,
    now: UtcClock,
    sleep: Sleep,
) -> None:
    assert job.lease_token is not None

    while True:
        await sleep(settings.lease_refresh.total_seconds())

        async with transaction():
            current_job = await get_media_alias_enrichment_job(job.id, with_for_update=True)
            # Sample the clock after acquiring the lock: a delayed heartbeat must
            # not resurrect a lease that expired while waiting for another transaction.
            timestamp = now()
            if not _owns_job(current_job=current_job, claim=job, now=timestamp):
                return

            owned = await refresh_media_alias_enrichment_job_lease(
                job_id=job.id,
                lease_token=job.lease_token,
                leased_until=timestamp + settings.lease_ttl,
                refreshed_at=timestamp,
            )

        if not owned:
            return


def _owns_job(*, current_job: MediaAliasEnrichmentJob | None, claim: MediaAliasEnrichmentJob, now: datetime) -> bool:
    return (
        current_job is not None
        and current_job.status == MediaAliasEnrichmentJobStatus.processing
        and current_job.lease_token == claim.lease_token
        and current_job.leased_until is not None
        and current_job.leased_until > now
    )


async def _handle_failure(
    *,
    job: MediaAliasEnrichmentJob,
    error: Exception,
    settings: MediaAliasEnrichmentSettings,
    now: UtcClock,
) -> str:
    retryable, result_class = _classify_failure(error)
    retry = retryable and job.retry_count < settings.max_attempts

    timestamp = now()
    available_at = (
        timestamp
        + media_alias_retry_delay(
            retry_count=job.retry_count,
            base=settings.retry_base,
            maximum=settings.retry_max,
        )
        if retry
        else timestamp
    )

    assert job.lease_token is not None
    async with transaction():
        owned = await finish_media_alias_enrichment_job(
            job_id=job.id,
            lease_token=job.lease_token,
            status=MediaAliasEnrichmentJobStatus.retry if retry else MediaAliasEnrichmentJobStatus.failed,
            result_class=result_class,
            finished_at=timestamp,
            available_at=available_at,
            last_error=type(error).__name__,
        )

    if not owned:
        return "lease_lost"

    _emit(CounterMetric("media_alias_enrichment.retry" if retry else "media_alias_enrichment.failed"))
    return result_class


def _classify_failure(error: Exception) -> tuple[bool, str]:
    match error:
        case _EnrichmentAttemptsExhaustedError():
            return False, "attempts_exhausted"
        case UnsupportedMediaAliasTypeError():
            return False, "unsupported"
        case MediaAliasDecodeError():
            return False, "decode_failed"
        case YandexDownloadTooLargeError():
            return False, "source_too_large"
        case MediaAliasFrameTooLargeError():
            return False, "frame_too_large"
        case PreparedMediaAliasImageTooLargeError():
            return False, "image_too_large"
        case MediaAliasImagePreparationError():
            return False, "prepare_failed"
        case OllamaHTTPError(status=status) | aiohttp.ClientResponseError(status=status):
            return status == 429 or status >= 500, f"http_{status}"
        case InvalidOllamaResponseError():
            return True, "invalid_output"
        case OllamaTransportError() | aiohttp.ClientError() | TimeoutError():
            return True, "network_error"
        case _:
            return True, "unexpected_error"


async def _cancel_and_release(
    *,
    job: MediaAliasEnrichmentJob,
    tasks: Sequence[JobTask],
    now: UtcClock,
) -> str:
    await _cancel_tasks(tasks)
    assert job.lease_token is not None

    try:
        async with transaction():
            owned = await finish_media_alias_enrichment_job(
                job_id=job.id,
                lease_token=job.lease_token,
                status=MediaAliasEnrichmentJobStatus.retry,
                result_class="cancelled",
                finished_at=now(),
            )
    except Exception as error:
        logger.error(
            "Failed to release cancelled media alias job job_id=%s exception_type=%s", job.id, type(error).__name__
        )
        return "cancelled_release_failed"

    if owned:
        _emit(CounterMetric("media_alias_enrichment.retry"))
        return "cancelled"
    return "lease_lost"


async def _cancel_tasks(tasks: Sequence[JobTask]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)


async def _wait_for_cleanup[T](cleanup: Awaitable[T]) -> T:
    task = asyncio.ensure_future(cleanup)

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue

    return await task


def _record_result(*, job: MediaAliasEnrichmentJob, result_class: str, stopwatch: Stopwatch) -> None:
    logger.info(
        "Media alias enrichment job_id=%s media_id=%s source_revision=%s "
        "attempt=%s model=%s duration_ms=%.1f result_class=%s",
        job.id,
        job.media_id,
        job.source_revision,
        job.attempt_count,
        job.model,
        stopwatch.elapsed_milliseconds(),
        result_class,
    )

    if result_class in {
        "succeeded",
        "stale_revision",
        "manual_aliases_won",
        "inactive",
        "unsupported",
        "decode_failed",
        "lease_lost",
    }:
        _emit(CounterMetric(f"media_alias_enrichment.{result_class}"))


def _emit(*metrics: Metric) -> None:
    try:
        get_metrics_sink().emit(metrics)
    except Exception:
        logger.error("Failed to emit media alias enrichment metrics")

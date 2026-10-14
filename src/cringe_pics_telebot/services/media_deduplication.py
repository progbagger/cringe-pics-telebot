import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath

from cringe_pics_telebot.repositories.yandex import (
    Image,
    YandexDestinationExistsError,
    create_directory,
    download_file,
    list_dir,
    move_resource,
)
from cringe_pics_telebot.services.media_duplicate_detection import MediaCandidate, ScanResult, find_duplicate_groups

logger = logging.getLogger(__name__)

DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_CATEGORY_BYTES = 1024 * 1024 * 1024
DEFAULT_DOWNLOAD_TIMEOUT = timedelta(minutes=2)
DEFAULT_MAX_IMAGE_PIXELS = 40_000_000
MAX_DESTINATION_ATTEMPTS = 1_000

type LeaseGuard = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class MediaDeduplicationSettings:
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_category_bytes: int = DEFAULT_MAX_CATEGORY_BYTES
    download_timeout: timedelta = DEFAULT_DOWNLOAD_TIMEOUT
    max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS

    def __post_init__(self) -> None:
        if self.max_file_bytes <= 0:
            raise ValueError("Media deduplication file byte limit must be positive")
        if self.max_category_bytes <= 0:
            raise ValueError("Media deduplication category byte limit must be positive")
        if self.download_timeout.total_seconds() <= 0:
            raise ValueError("Media deduplication download timeout must be positive")
        if self.max_image_pixels <= 0:
            raise ValueError("Media deduplication image pixel limit must be positive")


@dataclass(frozen=True, slots=True)
class MediaDeduplicationResult:
    images: tuple[Image, ...]
    groups: int = 0
    moved: int = 0
    move_failed: int = 0


class MediaDeduplicationError(RuntimeError): ...


class MediaDeduplicationLimitError(MediaDeduplicationError): ...


class MediaDeduplicationDownloadError(MediaDeduplicationError): ...


class MediaDeduplicationAnalysisError(MediaDeduplicationError): ...


async def deduplicate_category_media(
    *,
    category_directory: str,
    images: Sequence[Image],
    ensure_lease: LeaseGuard,
    settings: MediaDeduplicationSettings | None = None,
) -> MediaDeduplicationResult:
    if settings is None:
        settings = MediaDeduplicationSettings()

    eligible_images = tuple(image for image in images if _is_supported(image))
    if len(eligible_images) < 2:
        return MediaDeduplicationResult(images=tuple(images))

    _validate_declared_sizes(eligible_images, settings=settings)

    temporary_directory = tempfile.TemporaryDirectory(prefix="cringe-pics-media-deduplication-")
    root = Path(temporary_directory.name)
    try:
        candidates, images_by_path = await _download_candidates(
            eligible_images,
            root=root,
            settings=settings,
        )
        scan = await _run_blocking(
            find_duplicate_groups,
            candidates,
            max_image_pixels=settings.max_image_pixels,
        )
        if scan.errors:
            raise MediaDeduplicationAnalysisError("; ".join(scan.errors))

        result = await _move_duplicates(
            category_directory=category_directory,
            scan=scan,
            images_by_path=images_by_path,
            ensure_lease=ensure_lease,
        )
    finally:
        await _run_blocking(temporary_directory.cleanup)

    if not result.groups:
        return MediaDeduplicationResult(images=tuple(images))

    confirmed_images = tuple([image async for image in list_dir(category_directory)])
    return MediaDeduplicationResult(
        images=confirmed_images,
        groups=result.groups,
        moved=result.moved,
        move_failed=result.move_failed,
    )


def _validate_declared_sizes(images: Sequence[Image], *, settings: MediaDeduplicationSettings) -> None:
    declared_total = 0
    for image in images:
        if image.size is None:
            continue
        if image.size > settings.max_file_bytes:
            raise MediaDeduplicationLimitError(
                f"Media file exceeds deduplication byte limit: {image.path} ({image.size} > {settings.max_file_bytes})"
            )
        declared_total += image.size

    if declared_total > settings.max_category_bytes:
        raise MediaDeduplicationLimitError(
            f"Media category exceeds deduplication byte limit: {declared_total} > {settings.max_category_bytes}"
        )


async def _download_candidates(
    images: Sequence[Image],
    *,
    root: Path,
    settings: MediaDeduplicationSettings,
) -> tuple[tuple[MediaCandidate, ...], dict[Path, Image]]:
    candidates: list[MediaCandidate] = []
    images_by_path: dict[Path, Image] = {}
    downloaded_total = 0

    for index, image in enumerate(images):
        try:
            downloaded = await download_file(
                path=image.path,
                max_bytes=settings.max_file_bytes,
                timeout=settings.download_timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise MediaDeduplicationDownloadError(
                f"Failed to download media for deduplication: {image.path}"
            ) from error

        downloaded_total += len(downloaded.content)
        if downloaded_total > settings.max_category_bytes:
            raise MediaDeduplicationLimitError(
                f"Media category exceeds deduplication byte limit while downloading: "
                f"{downloaded_total} > {settings.max_category_bytes}"
            )

        suffix = PurePosixPath(image.path).suffix
        local_path = root / f"{index:06d}{suffix}"
        await _run_blocking(local_path.write_bytes, downloaded.content)

        candidates.append(
            MediaCandidate(
                path=local_path,
                compare_visually=image.mime_type.startswith("image/") and image.mime_type != "image/gif",
            )
        )
        images_by_path[local_path] = image

    return tuple(candidates), images_by_path


async def _move_duplicates(
    *,
    category_directory: str,
    scan: ScanResult,
    images_by_path: dict[Path, Image],
    ensure_lease: LeaseGuard,
) -> MediaDeduplicationResult:
    moved = 0
    move_failed = 0

    for group in scan.groups:
        kept = images_by_path[group.kept.path]
        quarantine_directory = _quarantine_directory(kept.path)

        try:
            await ensure_lease()
            await create_directory(quarantine_directory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Failed to create media duplicate quarantine category=%s kept=%s quarantine=%s",
                category_directory,
                kept.path,
                quarantine_directory,
            )
            move_failed += len(group.duplicates)
            continue

        for duplicate in group.duplicates:
            source = images_by_path[duplicate.path]
            try:
                await ensure_lease()
                destination = await _move_with_collision_retry(
                    source_path=source.path,
                    quarantine_directory=quarantine_directory,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to quarantine media duplicate category=%s kept=%s duplicate=%s",
                    category_directory,
                    kept.path,
                    source.path,
                )
                move_failed += 1
            else:
                moved += 1
                logger.info(
                    "Quarantined media duplicate category=%s kept=%s duplicate=%s destination=%s",
                    category_directory,
                    kept.path,
                    source.path,
                    destination,
                )

    return MediaDeduplicationResult(
        images=(),
        groups=len(scan.groups),
        moved=moved,
        move_failed=move_failed,
    )


async def _move_with_collision_retry(*, source_path: str, quarantine_directory: str) -> str:
    source = PurePosixPath(source_path)
    for attempt in range(1, MAX_DESTINATION_ATTEMPTS + 1):
        destination_name = source.name if attempt == 1 else f"{source.stem}-{attempt}{source.suffix}"
        destination = str(PurePosixPath(quarantine_directory) / destination_name)
        try:
            await move_resource(source_path=source_path, destination_path=destination)
        except YandexDestinationExistsError:
            continue

        return destination

    raise MediaDeduplicationError(
        f"Cannot find a free duplicate destination after {MAX_DESTINATION_ATTEMPTS} attempts: {source_path}"
    )


def _quarantine_directory(kept_path: str) -> str:
    kept = PurePosixPath(kept_path)
    return str(kept.parent / f"{kept.stem}-duplicates")


def _is_supported(image: Image) -> bool:
    return image.mime_type.startswith("image/") or image.mime_type == "video/mp4"


async def _run_blocking[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        logger.warning("Media deduplication cancelled; waiting for blocking work to finish")

        # A running thread cannot be cancelled safely; wait before temporary files are removed.
        while not work.done():
            try:
                await asyncio.wait({work})
            except asyncio.CancelledError:
                continue

        with suppress(Exception):
            work.result()
        raise

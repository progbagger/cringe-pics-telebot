import asyncio
import logging
from typing import cast

from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.repositories.yandex import Image, get_download_urls, list_dir
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia

MAX_INLINE_QUERY_RESULTS = 50

logger = logging.getLogger(__name__)


async def get_inline_images(
    subscription_type: SubscriptionType,
    *,
    limit: int | None = MAX_INLINE_QUERY_RESULTS,
) -> list[CachedMedia | LinkedMedia]:
    images: list[Image] = []
    async for image in list_dir(subscription_type.s3_directory_path):
        if limit is not None and len(images) >= limit:
            break
        images.append(image)

    cached_file_ids = await _get_cached_file_ids(images)
    uncached_images = [image for image, file_id in zip(images, cached_file_ids, strict=True) if file_id is None]
    download_urls_by_path = dict(
        zip(
            (image.path for image in uncached_images),
            await get_download_urls(image.path for image in uncached_images) if uncached_images else [],
            strict=True,
        )
    )

    results: list[CachedMedia | LinkedMedia] = []
    for image, file_id in zip(images, cached_file_ids, strict=True):
        if file_id is not None:
            results.append(
                CachedMedia(
                    name=image.name,
                    mime_type=image.mime_type,
                    path=image.path,
                    id=file_id,
                )
            )
        else:
            if (url := download_urls_by_path[image.path]) is None:
                continue

            results.append(
                LinkedMedia(
                    name=image.name,
                    mime_type=image.mime_type,
                    path=image.path,
                    url=url,
                )
            )

    return results


async def _get_cached_file_ids(images: list[Image]) -> list[str | None]:
    results = await asyncio.gather(
        *(cache.get(key=image.path, cls=str) for image in images),
        return_exceptions=True,
    )

    for image, result in zip(images, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            logger.error("Failed to get cached Telegram file ID for %s", image.path, exc_info=result)

    return [None if isinstance(result, BaseException) else cast(str | None, result) for result in results]

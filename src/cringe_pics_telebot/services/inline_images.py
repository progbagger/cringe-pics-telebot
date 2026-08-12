from dataclasses import dataclass

from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.repositories.yandex import Image, get_download_urls, list_dir

MAX_INLINE_QUERY_RESULTS = 50


@dataclass(slots=True)
class CachedInlineImage(Image):
    file_id: str


@dataclass(slots=True)
class LinkedInlineImage(Image):
    url: str


async def get_inline_images(
    subscription_type: SubscriptionType,
    *,
    limit: int = MAX_INLINE_QUERY_RESULTS,
) -> list[CachedInlineImage | LinkedInlineImage]:
    images: list[Image] = []
    async for image in list_dir(subscription_type.s3_directory_path):
        if len(images) >= limit:
            break
        images.append(image)

    cached_file_ids = [await cache.get(key=image.path, cls=str) for image in images]
    uncached_images = [image for image, file_id in zip(images, cached_file_ids, strict=True) if file_id is None]
    download_urls = iter(await get_download_urls(image.path for image in uncached_images) if uncached_images else [])

    results: list[CachedInlineImage | LinkedInlineImage] = []
    for image, file_id in zip(images, cached_file_ids, strict=True):
        if file_id is not None:
            results.append(
                CachedInlineImage(
                    name=image.name,
                    mime_type=image.mime_type,
                    path=image.path,
                    file_id=file_id,
                )
            )
        else:
            results.append(
                LinkedInlineImage(
                    name=image.name,
                    mime_type=image.mime_type,
                    path=image.path,
                    url=next(download_urls),
                )
            )

    return results

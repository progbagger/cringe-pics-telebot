import random
from dataclasses import dataclass
from datetime import timedelta

from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import get_subscription_types
from cringe_pics_telebot.repositories.yandex import Image, download_file, list_dir


@dataclass
class DownloadedImage(Image):
    data: bytes
    """Данные скачанной картинки"""


@dataclass
class CachedImage(Image):
    id: str
    """ID of picture on Telegram servers"""


async def get_random_image(category_id: int | None = None) -> DownloadedImage | CachedImage:
    subscription_types = {subscription.id: subscription for subscription in await get_subscription_types()}
    if category_id is None:
        category = random.choice(list(subscription_types.values()))
    else:
        category = subscription_types[category_id]

    random_image = random.choice([image async for image in list_dir(category.s3_directory_path)])

    image_data = await get_image_by_path(random_image.path)
    match image_data:
        case bytes():
            return DownloadedImage(
                name=random_image.name,
                mime_type=random_image.mime_type,
                path=random_image.path,
                data=image_data,
            )

        case str():
            return CachedImage(
                name=random_image.name,
                mime_type=random_image.mime_type,
                path=random_image.path,
                id=image_data,
            )


async def get_image_by_path(image_path: str) -> bytes | str:
    if (cached_image := await cache.get(key=image_path, cls=str)) is not None:
        return cached_image

    return await download_file(image_path)


async def update_image_cache(*, image_path: str, image_id: str) -> None:
    await cache.set(key=image_path, value=image_id, ttl=timedelta(days=7))

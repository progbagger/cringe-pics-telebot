import random
from dataclasses import dataclass
from datetime import timedelta

from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import get_subscription_types
from cringe_pics_telebot.repositories.yandex import Image, get_download_urls, list_dir


@dataclass(slots=True)
class LinkedMedia(Image):
    url: str
    """Временная ссылка, по которой Telegram загрузит картинку"""


@dataclass(slots=True)
class CachedMedia(Image):
    id: str
    """Идентификатор картинки на серверах Telegram"""


async def get_random_image(category_id: int | None = None) -> LinkedMedia | CachedMedia:
    subscription_types = {subscription.id: subscription for subscription in await get_subscription_types()}
    if category_id is None:
        category = random.choice(list(subscription_types.values()))
    else:
        category = subscription_types[category_id]

    random_image = random.choice([image async for image in list_dir(category.s3_directory_path)])

    if (cached_image := await cache.get(key=random_image.path, cls=str)) is not None:
        return CachedMedia(
            name=random_image.name,
            mime_type=random_image.mime_type,
            path=random_image.path,
            id=cached_image,
        )

    download_url, *_ = await get_download_urls([random_image.path])
    if download_url is None:
        raise RuntimeError(f"Failed to get download URL for {random_image.path}")

    return LinkedMedia(
        name=random_image.name,
        mime_type=random_image.mime_type,
        path=random_image.path,
        url=download_url,
    )


async def update_image_cache(*, image_path: str, image_id: str) -> None:
    await cache.set(key=image_path, value=image_id, cls=str, ttl=timedelta(days=7))

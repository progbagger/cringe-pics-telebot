import random
from dataclasses import dataclass

from cringe_pics_telebot.repositories.postgres import get_subscription_types
from cringe_pics_telebot.repositories.yandex import download_file, list_dir
from cringe_pics_telebot.repositories.yandex.yandex import Image


@dataclass
class DownloadedImage(Image):
    data: bytes
    """Данные скачанной картинки"""


async def get_random_image(category_id: int | None = None) -> DownloadedImage:
    """Неоптимизировано на данный момент. После прикрутить кэш или CDN."""

    subscription_types = {subscription.id: subscription for subscription in await get_subscription_types()}
    if category_id is None:
        category = random.choice(list(subscription_types.values()))
    else:
        category = subscription_types[category_id]

    random_image = random.choice([image async for image in list_dir(category.s3_directory_path)])

    return DownloadedImage(
        name=random_image.name,
        mime_type=random_image.mime_type,
        path=random_image.path,
        data=await download_file(random_image.path),
    )

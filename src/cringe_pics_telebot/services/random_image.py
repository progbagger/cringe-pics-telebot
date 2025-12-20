import random

from cringe_pics_telebot.repositories.postgres import get_subscription_types
from cringe_pics_telebot.repositories.yandex import download_file, list_dir


async def get_random_image(category_id: int | None = None) -> bytes:
    """Неоптимизировано на данный момент. После прикрутить кэш или CDN."""

    subscription_types = await get_subscription_types()
    if category_id is None:
        category = random.choice(list(subscription_types.values()))
    else:
        category = subscription_types[category_id]

    random_image = random.choice(
        [image async for image in list_dir(category.s3_directory_path)]
    )

    return await download_file(random_image.path)

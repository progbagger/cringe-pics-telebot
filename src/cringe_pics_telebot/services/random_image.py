import random
from dataclasses import dataclass

from cringe_pics_telebot.repositories.postgres import CategoryMedia, get_category_media_by_subscription_types
from cringe_pics_telebot.repositories.yandex import Image


@dataclass(slots=True)
class LinkedMedia(Image):
    url: str
    """Временная ссылка, по которой Telegram загрузит картинку"""


@dataclass(slots=True)
class CachedMedia(Image):
    id: str
    """Идентификатор картинки на серверах Telegram"""


async def get_random_image(category_id: int | None = None) -> CategoryMedia:
    category_ids = None if category_id is None else [category_id]
    return random.choice(await get_category_media_by_subscription_types(category_ids))

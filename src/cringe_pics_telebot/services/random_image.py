import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaStatus,
    get_category_media_by_subscription_types,
)
from cringe_pics_telebot.repositories.yandex import Image

type MediaChooser = Callable[[Sequence[CategoryMedia]], CategoryMedia]


@dataclass(slots=True)
class LinkedMedia(Image):
    url: str
    """Временная ссылка, по которой Telegram загрузит картинку"""


@dataclass(slots=True)
class CachedMedia(Image):
    id: str
    """Идентификатор картинки на серверах Telegram"""


def choose_random_image(
    media: Sequence[CategoryMedia],
    *,
    chooser: MediaChooser | None = None,
) -> CategoryMedia:
    pending = [item for item in media if item.status is CategoryMediaStatus.pending]
    ready = [item for item in media if item.status is CategoryMediaStatus.ready]
    candidates = pending or ready
    if not candidates:
        raise NoCategoryMediaError
    return (chooser or random.choice)(candidates)


async def get_random_image(
    category_id: int | None = None,
    *,
    chooser: MediaChooser | None = None,
) -> CategoryMedia:
    category_ids = None if category_id is None else [category_id]
    media = await get_category_media_by_subscription_types(category_ids)
    return choose_random_image(media, chooser=chooser)


class NoCategoryMediaError(LookupError): ...

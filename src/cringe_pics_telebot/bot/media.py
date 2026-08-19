import logging

from aiogram import Bot
from aiogram.types import (
    InputMediaAnimation,
    InputMediaPhoto,
    Message,
)

from cringe_pics_telebot.bot.helpers import HasFileId
from cringe_pics_telebot.repositories.yandex import get_download_urls
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia

logger = logging.getLogger(__name__)


async def add_image_to_message(*, message: Message, image: LinkedMedia | CachedMedia) -> Message | bool:
    added_cached_image = False
    if isinstance(image, CachedMedia):
        try:
            edited_message = await message.edit_media(_input_media(image=image, media=image.id))
            logger.info("Added cached image %s from cache", image.path)
        except Exception:
            logger.exception("Failed to attach cached image %s to message %s", image.id, message.message_id)
        else:
            added_cached_image = True

    if not added_cached_image:
        media_url = image.url if isinstance(image, LinkedMedia) else await _get_download_url(image.path)
        edited_message = await message.edit_media(_input_media(image=image, media=media_url))
        logger.info("Added image %s by URL", image.path)

    return edited_message


async def send_image_to_chat(*, bot: Bot, chat_id: int, image: LinkedMedia | CachedMedia) -> Message:
    media = image.id if isinstance(image, CachedMedia) else image.url

    if _is_animation(image):
        return await bot.send_animation(chat_id=chat_id, animation=media)

    return await bot.send_photo(chat_id=chat_id, photo=media)


def get_message_media_file_id(message: Message) -> str:
    media: HasFileId
    if message.photo is not None:
        media, *_ = message.photo
    elif message.animation is not None:
        media = message.animation
    else:
        raise ValueError("Resulted message %s has no media", message.message_id)

    return media.file_id


def _input_media(
    *,
    image: LinkedMedia | CachedMedia,
    media: str,
) -> InputMediaAnimation | InputMediaPhoto:
    if _is_animation(image):
        return InputMediaAnimation(media=media)

    return InputMediaPhoto(media=media)


async def _get_download_url(path: str) -> str:
    download_url, *_ = await get_download_urls([path])
    if download_url is None:
        raise RuntimeError(f"Failed to get download URL for {path}")
    return download_url


def _is_animation(image: LinkedMedia | CachedMedia) -> bool:
    return "gif" in image.mime_type

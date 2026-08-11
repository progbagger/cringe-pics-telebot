import logging

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InputMediaAnimation,
    InputMediaPhoto,
    Message,
)

from cringe_pics_telebot.bot.helpers import HasFileId
from cringe_pics_telebot.services.random_image import CachedMedia, DownloadedMedia, download_image

logger = logging.getLogger(__name__)


async def add_image_to_message(*, message: Message, image: DownloadedMedia | CachedMedia) -> Message | bool:
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
        edited_message = await message.edit_media(_input_media(image=image, media=await _image_file(image)))
        logger.info("Downloaded and added image %s", image.path)

    return edited_message


async def send_image_to_chat(*, bot: Bot, chat_id: int, image: DownloadedMedia | CachedMedia) -> Message:
    media = image.id if isinstance(image, CachedMedia) else await _image_file(image)

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
    image: DownloadedMedia | CachedMedia,
    media: str | BufferedInputFile,
) -> InputMediaAnimation | InputMediaPhoto:
    if _is_animation(image):
        return InputMediaAnimation(media=media)

    return InputMediaPhoto(media=media)


async def _image_file(image: DownloadedMedia | CachedMedia) -> BufferedInputFile:
    image_data = image.data if isinstance(image, DownloadedMedia) else await download_image(image.path)
    return BufferedInputFile(image_data, _image_filename(image))


def _image_filename(image: DownloadedMedia | CachedMedia) -> str:
    if _is_animation(image):
        return f"{image.name}.gif"

    return image.name


def _is_animation(image: DownloadedMedia | CachedMedia) -> bool:
    return "gif" in image.mime_type

import logging

from aiogram import Bot
from aiogram.types import (
    InputMediaAnimation,
    InputMediaPhoto,
    Message,
)

from cringe_pics_telebot.bot.helpers import HasFileId
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia

logger = logging.getLogger(__name__)


async def add_image_to_message(*, message: Message, image: LinkedMedia | CachedMedia) -> Message | bool:
    media = image.id if isinstance(image, CachedMedia) else image.url
    edited_message = await message.edit_media(_input_media(image=image, media=media))
    logger.info("Added image %s using %s", image.path, "file_id" if isinstance(image, CachedMedia) else "URL")
    return edited_message


async def send_image_to_chat(*, bot: Bot, chat_id: int, image: LinkedMedia | CachedMedia) -> Message:
    media = image.id if isinstance(image, CachedMedia) else image.url

    if _is_animation(image):
        return await bot.send_animation(chat_id=chat_id, animation=media)

    return await bot.send_photo(chat_id=chat_id, photo=media)


def get_message_media_file_ids(message: Message) -> tuple[str, str]:
    media: HasFileId
    if message.photo is not None:
        media = message.photo[-1]
    elif message.animation is not None:
        media = message.animation
    else:
        raise ValueError("Resulted message %s has no media", message.message_id)

    return media.file_id, media.file_unique_id


def _input_media(
    *,
    image: LinkedMedia | CachedMedia,
    media: str,
) -> InputMediaAnimation | InputMediaPhoto:
    if _is_animation(image):
        return InputMediaAnimation(media=media)

    return InputMediaPhoto(media=media)


def _is_animation(image: LinkedMedia | CachedMedia) -> bool:
    return "gif" in image.mime_type

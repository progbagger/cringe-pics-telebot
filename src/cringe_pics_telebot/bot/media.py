from aiogram import Bot
from aiogram.types import Message

from cringe_pics_telebot.bot.helpers import HasFileId
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia


async def send_image_to_chat(*, bot: Bot, chat_id: int, image: LinkedMedia | CachedMedia) -> Message:
    media = image.id if isinstance(image, CachedMedia) else image.url

    if _is_animation(image):
        return await bot.send_animation(chat_id=chat_id, animation=media)
    if _is_video(image):
        return await bot.send_video(chat_id=chat_id, video=media)

    return await bot.send_photo(chat_id=chat_id, photo=media)


def get_message_media_file_ids(message: Message) -> tuple[str, str]:
    media: HasFileId
    if message.photo is not None:
        media = message.photo[-1]
    elif message.animation is not None:
        media = message.animation
    elif message.video is not None:
        media = message.video
    else:
        raise ValueError("Resulted message %s has no media", message.message_id)

    return media.file_id, media.file_unique_id


def _is_animation(image: LinkedMedia | CachedMedia) -> bool:
    return "gif" in image.mime_type


def _is_video(image: LinkedMedia | CachedMedia) -> bool:
    return image.mime_type == "video/mp4"

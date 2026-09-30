import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import (
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from cringe_pics_telebot.bot.helpers import HasFileId
from cringe_pics_telebot.repositories.postgres import TelegramMediaType
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MediaDeliveryReceipt:
    message: Message
    bot_api_method: str


async def add_image_to_message(*, message: Message, image: LinkedMedia | CachedMedia) -> MediaDeliveryReceipt:
    media = image.id if isinstance(image, CachedMedia) else image.url
    edited_message = await message.edit_media(_input_media(image=image, media=media))
    if not isinstance(edited_message, Message):
        raise ValueError(
            f"editMessageMedia returned {type(edited_message).__name__} instead of Message "
            f"for message {message.message_id}"
        )

    logger.info("Added image %s using %s", image.path, "file_id" if isinstance(image, CachedMedia) else "URL")
    return MediaDeliveryReceipt(message=edited_message, bot_api_method="editMessageMedia")


async def send_image_to_chat(*, bot: Bot, chat_id: int, image: LinkedMedia | CachedMedia) -> MediaDeliveryReceipt:
    media = image.id if isinstance(image, CachedMedia) else image.url

    if _is_animation(image):
        message = await bot.send_animation(chat_id=chat_id, animation=media)
        method = "sendAnimation"
    elif _is_video(image):
        message = await bot.send_video(chat_id=chat_id, video=media)
        method = "sendVideo"
    else:
        message = await bot.send_photo(chat_id=chat_id, photo=media)
        method = "sendPhoto"

    return MediaDeliveryReceipt(message=message, bot_api_method=method)


def get_message_media_file_ids(message: Message, *, expected_type: TelegramMediaType) -> tuple[str, str]:
    media: HasFileId
    if expected_type is TelegramMediaType.photo and message.photo is not None:
        media = message.photo[-1]
    elif expected_type is TelegramMediaType.animation and message.animation is not None:
        media = message.animation
    elif expected_type is TelegramMediaType.video and message.video is not None:
        media = message.video
    else:
        fields = ",".join(_present_media_fields(message)) or "none"
        raise ValueError(
            f"Resulted message {message.message_id} has unexpected media: "
            f"expected={expected_type.value} content_type={get_message_content_type(message)} present_fields={fields}"
        )

    return media.file_id, media.file_unique_id


def get_message_content_type(message: Message) -> str:
    return str(getattr(message.content_type, "value", message.content_type))


def _present_media_fields(message: Message) -> tuple[str, ...]:
    return tuple(field for field in ("photo", "animation", "video", "document") if getattr(message, field) is not None)


def _input_media(
    *,
    image: LinkedMedia | CachedMedia,
    media: str,
) -> InputMediaAnimation | InputMediaPhoto | InputMediaVideo:
    if _is_animation(image):
        return InputMediaAnimation(media=media)
    if _is_video(image):
        return InputMediaVideo(media=media)

    return InputMediaPhoto(media=media)


def _is_animation(image: LinkedMedia | CachedMedia) -> bool:
    return "gif" in image.mime_type


def _is_video(image: LinkedMedia | CachedMedia) -> bool:
    return image.mime_type == "video/mp4"

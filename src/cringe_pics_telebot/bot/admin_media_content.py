from collections.abc import Sequence

from aiogram.types import (
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    InputRichBlockAnimation,
    InputRichBlockParagraph,
    InputRichBlockPhoto,
    InputRichBlockUnion,
    InputRichBlockVideo,
    InputRichMessage,
)

from cringe_pics_telebot.repositories.postgres import TelegramMediaType
from cringe_pics_telebot.services.admin_media import AdminMediaPreview
from cringe_pics_telebot.services.random_image import CachedMedia


def create_admin_media_content(
    text: str,
    previews: Sequence[AdminMediaPreview],
    *,
    numbered: bool = False,
    hide_media: bool = False,
) -> InputRichMessage:
    blocks: list[InputRichBlockUnion] = [InputRichBlockParagraph(text=text)]

    for number, preview in enumerate(previews, start=1):
        if numbered:
            blocks.append(InputRichBlockParagraph(text=f"{number}. {preview.metadata.media.name}"))

        media = preview.media
        if media is None or hide_media:
            blocks.append(InputRichBlockParagraph(text="Предпросмотр недоступен. Алиасы можно редактировать."))
            continue

        source = media.id if isinstance(media, CachedMedia) else media.url

        match preview.metadata.media.telegram_media_type:
            case TelegramMediaType.photo:
                blocks.append(InputRichBlockPhoto(photo=InputMediaPhoto(media=source)))
            case TelegramMediaType.animation:
                blocks.append(InputRichBlockAnimation(animation=InputMediaAnimation(media=source)))
            case TelegramMediaType.video:
                blocks.append(InputRichBlockVideo(video=InputMediaVideo(media=source)))

    return InputRichMessage(blocks=blocks, skip_entity_detection=True)

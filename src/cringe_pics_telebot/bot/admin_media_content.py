from collections.abc import Sequence

from aiogram.types import (
    Animation,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    InputRichBlockAnimation,
    InputRichBlockParagraph,
    InputRichBlockPhoto,
    InputRichBlockUnion,
    InputRichBlockVideo,
    InputRichMessage,
    Message,
    PhotoSize,
    RichBlockAnimation,
    RichBlockPhoto,
    RichBlockVideo,
    Video,
)

from cringe_pics_telebot.repositories.postgres import TelegramMediaType
from cringe_pics_telebot.services.admin_media import AdminMediaMaterialization, AdminMediaPreview
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


def get_admin_media_materializations(
    content: InputRichMessage,
    response: Message | bool,
    previews: Sequence[AdminMediaPreview],
) -> tuple[AdminMediaMaterialization, ...]:
    if not isinstance(response, Message) or response.rich_message is None:
        # No returned rich message means there are no confirmed Telegram file IDs.
        return ()

    sent = content.blocks or []
    received = response.rich_message.blocks
    if len(sent) != len(received) or any(a.type != b.type for a, b in zip(sent, received, strict=True)):
        # Missing or changed blocks make positional media matching ambiguous.
        return ()

    if any(isinstance(block, RichBlockPhoto) and not block.photo for block in received):
        # An empty photo has no file ID; skipping it would shift subsequent matches.
        return ()

    shown = iter(preview for preview in previews if preview.media is not None)
    result = []
    file: PhotoSize | Animation | Video
    for block in received:
        match block:
            case RichBlockPhoto(photo=sizes) if sizes:
                file = max(sizes, key=lambda size: size.width * size.height)
            case RichBlockAnimation(animation=file) | RichBlockVideo(video=file):
                pass
            case _:
                continue

        preview = next(shown)
        if preview.metadata.media.telegram_media_type.value != block.type:
            return ()
        if isinstance(preview.media, CachedMedia):
            continue

        result.append(
            AdminMediaMaterialization(
                media_id=preview.metadata.media.id,
                source_revision=preview.metadata.media.source_revision,
                file_id=file.file_id,
                file_unique_id=file.file_unique_id,
            )
        )

    return tuple(result)

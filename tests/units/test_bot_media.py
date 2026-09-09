from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import InputMediaAnimation, InputMediaPhoto, InputMediaVideo, Message
from hamcrest import assert_that, equal_to, instance_of, same_instance

from cringe_pics_telebot.bot import media as bot_media
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia


@pytest.mark.parametrize(
    ("mime_type", "expected_type"),
    [
        ("image/png", InputMediaPhoto),
        ("image/gif", InputMediaAnimation),
        ("video/mp4", InputMediaVideo),
    ],
)
def test_input_media_matches_mime_type(
    mime_type: str,
    expected_type: type[InputMediaPhoto | InputMediaAnimation | InputMediaVideo],
) -> None:
    result = bot_media._input_media(image=_linked_media(mime_type), media="https://media.test/file")

    assert_that(result, instance_of(expected_type))
    assert_that(result.media, equal_to("https://media.test/file"))


@pytest.mark.parametrize(
    ("mime_type", "method_name", "argument_name"),
    [
        ("image/png", "send_photo", "photo"),
        ("image/gif", "send_animation", "animation"),
        ("video/mp4", "send_video", "video"),
    ],
)
async def test_send_image_to_chat_dispatches_native_media_method(
    mime_type: str,
    method_name: str,
    argument_name: str,
) -> None:
    bot = AsyncMock(spec=Bot)
    sent_message = cast(Message, object())
    getattr(bot, method_name).return_value = sent_message

    result = await bot_media.send_image_to_chat(bot=bot, chat_id=42, image=_cached_media(mime_type))

    assert_that(result, same_instance(sent_message))
    getattr(bot, method_name).assert_awaited_once_with(chat_id=42, **{argument_name: "telegram-file-id"})


@pytest.mark.parametrize(
    ("media_field", "file_id", "file_unique_id"),
    [
        ("photo", "photo-id", "photo-unique-id"),
        ("animation", "animation-id", "animation-unique-id"),
        ("video", "video-id", "video-unique-id"),
    ],
)
def test_get_message_media_file_ids_reads_native_media(
    media_field: str,
    file_id: str,
    file_unique_id: str,
) -> None:
    media_payload = {
        "file_id": file_id,
        "file_unique_id": file_unique_id,
        "width": 1,
        "height": 1,
    }
    if media_field != "photo":
        media_payload["duration"] = 1
    payload: dict[str, object] = {
        "message_id": 1,
        "date": datetime(2026, 9, 9, tzinfo=UTC),
        "chat": {"id": 42, "type": "private"},
        media_field: [media_payload] if media_field == "photo" else media_payload,
    }

    assert_that(
        bot_media.get_message_media_file_ids(Message.model_validate(payload)),
        equal_to((file_id, file_unique_id)),
    )


def _linked_media(mime_type: str) -> LinkedMedia:
    return LinkedMedia(
        name="media",
        mime_type=mime_type,
        path="day/media",
        source_revision="sha256:media",
        url="https://media.test/file",
    )


def _cached_media(mime_type: str) -> CachedMedia:
    return CachedMedia(
        name="media",
        mime_type=mime_type,
        path="day/media",
        source_revision="sha256:media",
        id="telegram-file-id",
    )

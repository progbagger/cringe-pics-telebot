import asyncio
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import (
    AnswerCallbackQuery,
    AnswerInlineQuery,
    CopyMessage,
    CopyMessages,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    ForwardMessage,
    Response,
    SendAnimation,
    SendAudio,
    SendContact,
    SendDice,
    SendDocument,
    SendLivePhoto,
    SendLocation,
    SendMediaGroup,
    SendMessage,
    SendMessageDraft,
    SendPaidMedia,
    SendPhoto,
    SendPoll,
    SendRichMessage,
    SendSticker,
    SendVenue,
    SendVideo,
    SendVideoNote,
    SendVoice,
    TelegramMethod,
)
from aiogram.types import (
    ForceReply,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputPaidMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    ReplyMarkupUnion,
)
from hamcrest import assert_that, equal_to, not_, same_instance

from cringe_pics_telebot.bot.main_keyboard import MainKeyboardMiddleware, main_keyboard_recipient
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import SubscriptionType
from cringe_pics_telebot.services import main_keyboard
from cringe_pics_telebot.services.main_keyboard import MainKeyboardCache


@pytest.mark.parametrize(
    "method",
    [
        SendMessage(chat_id=42, text="text"),
        SendPhoto(chat_id=42, photo="photo"),
        SendAnimation(chat_id=42, animation="animation"),
        SendVideo(chat_id=42, video="video"),
        CopyMessage(chat_id=42, from_chat_id=999, message_id=1),
        SendAudio(chat_id=42, audio="audio"),
        SendDocument(chat_id=42, document="document"),
        SendVoice(chat_id=42, voice="voice"),
        SendVideoNote(chat_id=42, video_note="video"),
        SendSticker(chat_id=42, sticker="sticker"),
        SendContact(chat_id=42, phone_number="123", first_name="name"),
        SendLocation(chat_id=42, latitude=0, longitude=0),
        SendVenue(chat_id=42, latitude=0, longitude=0, title="title", address="address"),
        SendPoll(chat_id=42, question="question", options=["a", "b"]),
        SendDice(chat_id=42),
        SendPaidMedia(chat_id=42, star_count=1, media=[InputPaidMediaPhoto(media="photo")]),
        SendLivePhoto(chat_id=42, photo="photo", live_photo="video"),
        SendRichMessage(chat_id=42, rich_message={"blocks": []}),
    ],
    ids=lambda method: method.__api_method__,
)
def test_new_messages_with_reply_markup_support_use_private_recipient(method: TelegramMethod[Any]) -> None:
    assert main_keyboard_recipient(method) == 42


@pytest.mark.parametrize(
    "chat_id,expected", [(42, 42), ("42", 42), (-42, None), ("-10042", None), (0, None), ("@channel", None)]
)
def test_main_keyboard_is_only_selected_for_positive_numeric_private_chat_ids(
    chat_id: int | str, expected: int | None
) -> None:
    assert main_keyboard_recipient(SendMessage(chat_id=chat_id, text="text")) == expected


@pytest.mark.parametrize(
    "markup",
    [
        InlineKeyboardMarkup(inline_keyboard=[]),
        ReplyKeyboardRemove(remove_keyboard=True),
        ForceReply(force_reply=True),
        ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="special")]]),
    ],
)
def test_explicit_markup_has_priority(markup: ReplyMarkupUnion) -> None:
    assert main_keyboard_recipient(SendMessage(chat_id=42, text="text", reply_markup=markup)) is None


def test_business_message_does_not_receive_main_keyboard() -> None:
    assert main_keyboard_recipient(SendMessage(chat_id=42, text="text", business_connection_id="business")) is None


@pytest.mark.parametrize(
    "method",
    [
        EditMessageText(chat_id=42, message_id=1, text="text"),
        EditMessageReplyMarkup(chat_id=42, message_id=1),
        EditMessageMedia(chat_id=42, message_id=1, media={"type": "photo", "media": "photo"}),
        SendMediaGroup(chat_id=42, media=[InputMediaPhoto(media="photo")]),
        CopyMessages(chat_id=42, from_chat_id=999, message_ids=[1]),
        ForwardMessage(chat_id=42, from_chat_id=999, message_id=1),
        AnswerCallbackQuery(callback_query_id="callback"),
        AnswerInlineQuery(inline_query_id="inline", results=[]),
        SendMessageDraft(chat_id=42, draft_id=1, text="text"),
    ],
    ids=lambda method: method.__api_method__,
)
def test_edits_albums_callbacks_and_drafts_do_not_receive_main_keyboard(method: TelegramMethod[Any]) -> None:
    assert main_keyboard_recipient(method) is None


async def test_snapshot_expiry_is_fixed_and_includes_exact_ttl_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    loads = 0

    async def categories() -> list[SubscriptionType]:
        nonlocal loads
        loads += 1
        return []

    async def administrators() -> frozenset[int]:
        return frozenset({loads})

    monkeypatch.setattr(main_keyboard, "get_active_subscription_types", categories)
    monkeypatch.setattr(main_keyboard, "get_administrator_ids", administrators)
    cache = MainKeyboardCache(clock=lambda: now)

    first = await cache.get_snapshot()
    for timestamp in (101.0, 130.0, 159.999):
        now = timestamp
        assert_that(await cache.get_snapshot(), same_instance(first))

    now = 160.0
    second = await cache.get_snapshot()
    assert_that(second, not_(same_instance(first)))
    assert_that(second.administrator_ids, equal_to(frozenset({2})))

    now = 1000.0
    assert_that(await cache.get_snapshot(), not_(same_instance(second)))
    assert loads == 3


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_failed_or_cancelled_refresh_preserves_expiry_and_releases_lock(
    error_type: type[BaseException], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 0.0
    fail = False
    administrator_ids = frozenset({42})

    async def categories() -> list[SubscriptionType]:
        return []

    async def administrators() -> frozenset[int]:
        if fail:
            raise error_type()
        return administrator_ids

    monkeypatch.setattr(main_keyboard, "get_active_subscription_types", categories)
    monkeypatch.setattr(main_keyboard, "get_administrator_ids", administrators)
    cache = MainKeyboardCache(clock=lambda: now)

    first = await cache.get_snapshot()
    now = 60.0
    fail = True
    with pytest.raises(error_type):
        await cache.get_snapshot()

    fail = False
    administrator_ids = frozenset()
    async with asyncio.timeout(1):
        refreshed = await cache.get_snapshot()

    assert_that(refreshed, not_(same_instance(first)))
    assert_that(first.administrator_ids, equal_to(frozenset({42})))
    assert_that(refreshed.administrator_ids, equal_to(frozenset()))


async def test_slow_snapshot_read_does_not_extend_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 0.0

    async def categories() -> list[SubscriptionType]:
        nonlocal now
        now += 60
        return []

    async def administrators() -> frozenset[int]:
        return frozenset()

    monkeypatch.setattr(main_keyboard, "get_active_subscription_types", categories)
    monkeypatch.setattr(main_keyboard, "get_administrator_ids", administrators)
    cache = MainKeyboardCache(clock=lambda: now)
    first = await cache.get_snapshot()
    assert_that(await cache.get_snapshot(), not_(same_instance(first)))


async def test_reused_methods_remain_unchanged_and_share_markup_until_snapshot_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    administrator_ids = frozenset({43})
    sent: list[SendMessage] = []

    async def categories() -> list[SubscriptionType]:
        return []

    async def administrators() -> frozenset[int]:
        return administrator_ids

    async def capture(bot: Bot, method: TelegramMethod[Any]) -> Response[Any]:
        assert isinstance(method, SendMessage)
        sent.append(method)
        return Response(ok=True, result=True)

    monkeypatch.setattr(main_keyboard, "get_active_subscription_types", categories)
    monkeypatch.setattr(main_keyboard, "get_administrator_ids", administrators)
    middleware = MainKeyboardMiddleware(MainKeyboardCache(clock=lambda: now))
    bot = Bot("123456:unit-test-token")
    method = SendMessage(chat_id=42, text="reused")

    try:
        await middleware(capture, bot, method)
        await middleware(capture, bot, SendMessage(chat_id=44, text="regular"))
        await middleware(capture, bot, SendMessage(chat_id=43, text="admin"))

        assert_that(sent[1].reply_markup, same_instance(sent[0].reply_markup))
        assert_that(sent[2].reply_markup, not_(same_instance(sent[0].reply_markup)))

        administrator_ids = frozenset({42})
        now = 60.0
        await middleware(capture, bot, method)

        assert method.reply_markup is None
        assert_that(sent[3].reply_markup, not_(same_instance(sent[0].reply_markup)))
        refreshed_markup = sent[3].reply_markup
        assert isinstance(refreshed_markup, ReplyKeyboardMarkup)
        assert_that(refreshed_markup.keyboard[0][0].text, equal_to("Админ-панель"))
    finally:
        await bot.session.close()

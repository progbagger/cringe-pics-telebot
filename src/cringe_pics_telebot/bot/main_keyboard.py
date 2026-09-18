from dataclasses import dataclass

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import (
    CopyMessage,
    Response,
    SendAnimation,
    SendAudio,
    SendContact,
    SendDice,
    SendDocument,
    SendLivePhoto,
    SendLocation,
    SendMessage,
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
from aiogram.methods.base import TelegramType
from aiogram.types import ReplyKeyboardMarkup

from cringe_pics_telebot.services.main_keyboard import MainKeyboardCache, MainKeyboardSnapshot

from .keyboards import create_reply_keyboard

_MAIN_KEYBOARD_METHODS = (
    SendMessage,
    SendPhoto,
    SendAnimation,
    SendVideo,
    CopyMessage,
    SendAudio,
    SendDocument,
    SendVoice,
    SendVideoNote,
    SendSticker,
    SendContact,
    SendLocation,
    SendVenue,
    SendPoll,
    SendDice,
    SendPaidMedia,
    SendLivePhoto,
    SendRichMessage,
)


def main_keyboard_recipient(method: TelegramMethod[TelegramType]) -> int | None:
    if not isinstance(method, _MAIN_KEYBOARD_METHODS) or method.reply_markup is not None:
        return None

    if getattr(method, "business_connection_id", None) is not None:
        return None

    try:
        chat_id = int(method.chat_id)
    except ValueError:
        return None

    return chat_id if chat_id > 0 else None


@dataclass(frozen=True, slots=True)
class _MainKeyboards:
    snapshot: MainKeyboardSnapshot
    regular: ReplyKeyboardMarkup
    administrator: ReplyKeyboardMarkup


class MainKeyboardMiddleware(BaseRequestMiddleware):
    def __init__(self, cache: MainKeyboardCache) -> None:
        self._cache = cache
        self._keyboards: _MainKeyboards | None = None

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        recipient_id = main_keyboard_recipient(method)
        if recipient_id is not None:
            snapshot = await self._cache.get_snapshot()
            keyboards = self._keyboards

            if keyboards is None or keyboards.snapshot is not snapshot:
                keyboards = _MainKeyboards(
                    snapshot=snapshot,
                    regular=create_reply_keyboard(snapshot.categories),
                    administrator=create_reply_keyboard(snapshot.categories, is_admin=True),
                )
                self._keyboards = keyboards

            markup = keyboards.administrator if recipient_id in snapshot.administrator_ids else keyboards.regular
            # Do not turn our default into explicit markup on a reused method.
            method = method.model_copy(update={"reply_markup": markup})

        return await make_request(bot, method)

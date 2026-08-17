from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from cringe_pics_telebot.repositories.postgres import create_user, transaction


class RegisterPrivateUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None and event.chat.type == ChatType.PRIVATE:
            async with transaction():
                await create_user(event.from_user.id)

        return await handler(event, data)

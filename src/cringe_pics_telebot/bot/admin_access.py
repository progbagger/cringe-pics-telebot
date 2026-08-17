from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from cringe_pics_telebot.repositories.postgres import is_administrator


class IsAdministrator(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if event.from_user is None:
            return False
        return await is_administrator(event.from_user.id)

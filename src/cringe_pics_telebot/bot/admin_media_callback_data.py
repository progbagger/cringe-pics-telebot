from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminMediaAction(StrEnum):
    media_list = "l"
    media = "m"
    edit_aliases = "e"
    clear_aliases = "c"
    cancel_form = "x"


class AdminMediaCallbackData(CallbackData, prefix="am"):
    action: AdminMediaAction
    category_id: int
    media_id: int = 0
    category_page: int = 0
    media_page: int = 0

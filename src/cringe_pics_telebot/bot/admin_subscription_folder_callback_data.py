from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminSubscriptionFolderAction(StrEnum):
    folders = "l"
    folder = "o"
    create = "c"
    rename = "r"
    toggle_category = "t"
    delete = "d"
    confirm_delete = "x"
    cancel_form = "q"


class AdminSubscriptionFolderCallbackData(CallbackData, prefix="af"):
    action: AdminSubscriptionFolderAction
    folder_id: int = 0
    category_id: int = 0
    folder_page: int = 0
    category_page: int = 0

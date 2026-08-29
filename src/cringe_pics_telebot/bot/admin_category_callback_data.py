from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminCategoryAction(StrEnum):
    categories = "categories"
    category = "category"
    create = "create"
    activate = "activate"
    deactivate = "deactivate"
    edit_aliases = "edit_aliases"
    clear_aliases = "clear_aliases"
    cancel_form = "cancel"


class AdminCategoryCallbackData(CallbackData, prefix="admin_category"):
    action: AdminCategoryAction
    category_id: int = 0

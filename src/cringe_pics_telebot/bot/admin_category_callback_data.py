from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminCategoryAction(StrEnum):
    categories = "categories"
    category = "category"
    create = "create"
    create_scheduled = "create_scheduled"
    create_without_schedule = "create_without_schedule"
    activate = "activate"
    deactivate = "deactivate"
    edit_time = "edit_time"
    disable_schedule = "disable_schedule"
    edit_weekdays = "edit_days"
    toggle_weekday = "toggle_day"
    confirm_weekdays = "save_days"
    daily_weekdays = "daily"
    edit_aliases = "edit_aliases"
    clear_aliases = "clear_aliases"
    cancel_form = "cancel"


class AdminCategoryCallbackData(CallbackData, prefix="admin_category"):
    action: AdminCategoryAction
    category_id: int = 0
    weekday: int = 0

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminAction(StrEnum):
    panel = "panel"
    broadcasts = "broadcasts"
    new_broadcast = "new"
    edit_broadcast = "edit"
    edit_message = "message"
    edit_schedule = "schedule"
    delete_broadcast = "delete"
    confirm_delete = "confirm_delete"
    cancel_form = "cancel"


class AdminCallbackData(CallbackData, prefix="admin"):
    action: AdminAction
    broadcast_id: int = 0

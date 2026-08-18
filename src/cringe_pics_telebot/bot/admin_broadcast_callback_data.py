from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminBroadcastAction(StrEnum):
    broadcasts = "broadcasts"
    new_broadcast = "new"
    edit_broadcast = "edit"
    edit_message = "message"
    edit_schedule = "schedule"
    edit_recipients = "recipients"
    delete_broadcast = "delete"
    confirm_delete = "confirm_delete"
    cancel_form = "cancel"
    skip_recipients = "skip_recipients"


class AdminBroadcastCallbackData(CallbackData, prefix="admin_broadcast"):
    action: AdminBroadcastAction
    broadcast_id: int = 0

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminBroadcastAction(StrEnum):
    broadcasts = "broadcasts"
    page = "page"
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
    """Legacy callback data kept for messages sent before keyboard pagination."""

    action: AdminBroadcastAction
    broadcast_id: int = 0


class AdminBroadcastPagedCallbackData(CallbackData, prefix="abp"):
    action: AdminBroadcastAction
    broadcast_id: int = 0
    page: int = 0

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminPanelAction(StrEnum):
    panel = "panel"
    synchronize_media = "synchronize_media"


class AdminPanelCallbackData(CallbackData, prefix="admin_panel"):
    action: AdminPanelAction

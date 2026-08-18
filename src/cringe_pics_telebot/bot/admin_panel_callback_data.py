from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AdminPanelAction(StrEnum):
    panel = "panel"


class AdminPanelCallbackData(CallbackData, prefix="admin_panel"):
    action: AdminPanelAction

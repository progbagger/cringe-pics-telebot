from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from cringe_pics_telebot.repositories.postgres.entities import AdminBroadcast
from cringe_pics_telebot.services.admin_broadcast_schedules import format_admin_broadcast_schedule

from .admin_callback_data import AdminAction, AdminCallbackData


def create_admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Рассылки", callback_data=_callback(AdminAction.broadcasts))
    return builder.as_markup()


def create_admin_broadcasts_keyboard(broadcasts: Iterable[AdminBroadcast]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for broadcast in broadcasts:
        builder.row(
            InlineKeyboardButton(
                text=format_admin_broadcast_schedule(
                    broadcast.scheduled_local_at,
                    broadcast.timezone_offset_minutes,
                    short=True,
                ),
                callback_data=_callback(AdminAction.edit_broadcast, broadcast.id),
            ),
            InlineKeyboardButton(
                text="✏️",
                callback_data=_callback(AdminAction.edit_broadcast, broadcast.id),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=_callback(AdminAction.delete_broadcast, broadcast.id),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="Новая рассылка",
            callback_data=_callback(AdminAction.new_broadcast),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад",
            callback_data=_callback(AdminAction.panel),
        )
    )
    return builder.as_markup()


def create_admin_broadcast_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Изменить сообщение",
        callback_data=_callback(AdminAction.edit_message, broadcast_id),
    )
    builder.button(
        text="Изменить дату и время",
        callback_data=_callback(AdminAction.edit_schedule, broadcast_id),
    )
    builder.button(
        text="Удалить",
        callback_data=_callback(AdminAction.delete_broadcast, broadcast_id),
    )
    builder.button(
        text="Назад",
        callback_data=_callback(AdminAction.broadcasts),
    )
    builder.adjust(1)
    return builder.as_markup()


def create_admin_broadcast_delete_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Удалить",
        callback_data=_callback(AdminAction.confirm_delete, broadcast_id),
    )
    builder.button(
        text="Отмена",
        callback_data=_callback(AdminAction.edit_broadcast, broadcast_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def create_admin_form_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data=_callback(AdminAction.cancel_form))
    return builder.as_markup()


def _callback(action: AdminAction, broadcast_id: int = 0) -> str:
    return AdminCallbackData(action=action, broadcast_id=broadcast_id).pack()

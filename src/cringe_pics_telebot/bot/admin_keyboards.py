from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from cringe_pics_telebot.repositories.postgres.entities import AdminBroadcast, SubscriptionType
from cringe_pics_telebot.services.admin_broadcast_schedules import format_admin_broadcast_schedule

from .admin_broadcast_callback_data import AdminBroadcastAction, AdminBroadcastCallbackData
from .admin_category_callback_data import AdminCategoryAction, AdminCategoryCallbackData
from .admin_panel_callback_data import AdminPanelAction, AdminPanelCallbackData


def create_admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Рассылки",
        callback_data=_broadcast_callback(AdminBroadcastAction.broadcasts),
    )
    builder.button(
        text="Управление категориями",
        callback_data=_category_callback(AdminCategoryAction.categories),
    )
    builder.button(
        text="Синхронизировать медиа",
        callback_data=_admin_panel_callback(AdminPanelAction.synchronize_media),
    )
    builder.adjust(1)
    return builder.as_markup()


def create_admin_media_sync_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Синхронизировать повторно",
        callback_data=_admin_panel_callback(AdminPanelAction.synchronize_media),
    )
    builder.button(text="Назад", callback_data=_panel_callback())
    builder.adjust(1)
    return builder.as_markup()


def create_admin_categories_keyboard(subscription_types: Iterable[SubscriptionType]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for subscription_type in sorted(subscription_types, key=lambda item: item.name.casefold()):
        status = "активна" if subscription_type.is_active else "неактивна"
        icon = "✅" if subscription_type.is_active else "⏸"
        builder.button(
            text=f"{icon} {subscription_type.name} — {status}",
            callback_data=_category_callback(AdminCategoryAction.category, subscription_type.id),
        )
    builder.button(text="Создать категорию", callback_data=_category_callback(AdminCategoryAction.create))
    builder.button(text="Назад", callback_data=_panel_callback())
    builder.adjust(1)
    return builder.as_markup()


def create_admin_category_keyboard(
    category_id: int,
    *,
    has_aliases: bool,
    is_active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Деактивировать" if is_active else "Активировать",
        callback_data=_category_callback(
            AdminCategoryAction.deactivate if is_active else AdminCategoryAction.activate,
            category_id,
        ),
    )
    builder.button(
        text="Изменить алиасы",
        callback_data=_category_callback(AdminCategoryAction.edit_aliases, category_id),
    )
    if has_aliases:
        builder.button(
            text="Очистить алиасы",
            callback_data=_category_callback(AdminCategoryAction.clear_aliases, category_id),
        )
    builder.button(
        text="Назад",
        callback_data=_category_callback(AdminCategoryAction.categories),
    )
    builder.adjust(1)
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
                callback_data=_broadcast_callback(AdminBroadcastAction.edit_broadcast, broadcast.id),
            ),
            InlineKeyboardButton(
                text="✏️",
                callback_data=_broadcast_callback(AdminBroadcastAction.edit_broadcast, broadcast.id),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=_broadcast_callback(AdminBroadcastAction.delete_broadcast, broadcast.id),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="Новая рассылка",
            callback_data=_broadcast_callback(AdminBroadcastAction.new_broadcast),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад",
            callback_data=_panel_callback(),
        )
    )
    return builder.as_markup()


def create_admin_broadcast_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Изменить сообщение",
        callback_data=_broadcast_callback(AdminBroadcastAction.edit_message, broadcast_id),
    )
    builder.button(
        text="Изменить дату и время",
        callback_data=_broadcast_callback(AdminBroadcastAction.edit_schedule, broadcast_id),
    )
    builder.button(
        text="Изменить дополнительные ID",
        callback_data=_broadcast_callback(AdminBroadcastAction.edit_recipients, broadcast_id),
    )
    builder.button(
        text="Удалить",
        callback_data=_broadcast_callback(AdminBroadcastAction.delete_broadcast, broadcast_id),
    )
    builder.button(
        text="Назад",
        callback_data=_broadcast_callback(AdminBroadcastAction.broadcasts),
    )
    builder.adjust(1)
    return builder.as_markup()


def create_admin_broadcast_delete_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Удалить",
        callback_data=_broadcast_callback(AdminBroadcastAction.confirm_delete, broadcast_id),
    )
    builder.button(
        text="Отмена",
        callback_data=_broadcast_callback(AdminBroadcastAction.edit_broadcast, broadcast_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def create_admin_form_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Отмена",
        callback_data=_broadcast_callback(AdminBroadcastAction.cancel_form),
    )
    return builder.as_markup()


def create_admin_category_form_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Отмена",
        callback_data=_category_callback(AdminCategoryAction.cancel_form),
    )
    return builder.as_markup()


def create_admin_recipients_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Пропустить",
        callback_data=_broadcast_callback(AdminBroadcastAction.skip_recipients),
    )
    builder.button(
        text="Отмена",
        callback_data=_broadcast_callback(AdminBroadcastAction.cancel_form),
    )
    builder.adjust(1)
    return builder.as_markup()


def _broadcast_callback(action: AdminBroadcastAction, broadcast_id: int = 0) -> str:
    return AdminBroadcastCallbackData(action=action, broadcast_id=broadcast_id).pack()


def _category_callback(action: AdminCategoryAction, category_id: int = 0) -> str:
    return AdminCategoryCallbackData(action=action, category_id=category_id).pack()


def _admin_panel_callback(action: AdminPanelAction) -> str:
    return AdminPanelCallbackData(action=action).pack()


def _panel_callback() -> str:
    return _admin_panel_callback(AdminPanelAction.panel)

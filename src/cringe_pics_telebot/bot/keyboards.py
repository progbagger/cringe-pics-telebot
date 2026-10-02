from collections.abc import Iterable
from datetime import time

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from cringe_pics_telebot.bot.emojis import Emoji
from cringe_pics_telebot.bot.inline_pagination import paginate_inline_keyboard
from cringe_pics_telebot.bot.subscription_callback_data import (
    SubscriptionFolderAction,
    SubscriptionFolderCallbackData,
    SubscriptionMenuActionCallbackData,
    SubscriptionPageCallbackData,
    pack_subscription_pages,
)
from cringe_pics_telebot.entities.subscription_menu import (
    SubscriptionFolderMenu,
    UserSubscriptionMenu,
)
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import (
    SubscriptionType,
)
from cringe_pics_telebot.services.subscription_schedules import format_subscription_schedule


def create_inline_subscriptions_keyboard(
    menu: UserSubscriptionMenu,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    inline_keyboard_builder = InlineKeyboardBuilder()
    items: list[SubscriptionFolderMenu | SubscriptionInfo] = [
        *sorted(menu.folders.values(), key=_subscription_folder_sort_key),
        *sorted(menu.ungrouped_subscriptions, key=_subscription_sort_key),
    ]
    current_page = paginate_inline_keyboard(items, page)

    for item in current_page.items:
        if isinstance(item, SubscriptionFolderMenu):
            inline_keyboard_builder.button(
                text=f"📁 {item.folder.name}",
                callback_data=SubscriptionFolderCallbackData(
                    action=SubscriptionFolderAction.open,
                    folder_id=item.folder.id,
                    pages=pack_subscription_pages(root_page=current_page.number, folder_page=0),
                ),
            )
        else:
            _add_subscription_button(
                inline_keyboard_builder,
                item,
                folder_id=0,
                root_page=current_page.number,
                folder_page=0,
            )

    inline_keyboard_builder.adjust(1, repeat=True)
    navigation = []
    if current_page.previous_number is not None:
        navigation.append(
            InlineKeyboardButton(
                text="<",
                callback_data=SubscriptionPageCallbackData(page=current_page.previous_number).pack(),
            )
        )
    if current_page.next_number is not None:
        navigation.append(
            InlineKeyboardButton(
                text=">",
                callback_data=SubscriptionPageCallbackData(page=current_page.next_number).pack(),
            )
        )
    if navigation:
        inline_keyboard_builder.row(*navigation)
    return inline_keyboard_builder.as_markup()


def create_inline_subscription_folder_keyboard(
    folder: SubscriptionFolderMenu,
    *,
    root_page: int,
    folder_page: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    subscriptions = sorted(folder.subscriptions, key=_subscription_sort_key)
    current_page = paginate_inline_keyboard(subscriptions, folder_page)

    for subscription in current_page.items:
        _add_subscription_button(
            builder,
            subscription,
            folder_id=folder.folder.id,
            root_page=root_page,
            folder_page=current_page.number,
        )

    builder.adjust(1, repeat=True)
    navigation = []
    if current_page.previous_number is not None:
        navigation.append(
            InlineKeyboardButton(
                text="<",
                callback_data=_folder_callback(
                    SubscriptionFolderAction.page,
                    folder_id=folder.folder.id,
                    root_page=root_page,
                    folder_page=current_page.previous_number,
                ),
            )
        )
    if current_page.next_number is not None:
        navigation.append(
            InlineKeyboardButton(
                text=">",
                callback_data=_folder_callback(
                    SubscriptionFolderAction.page,
                    folder_id=folder.folder.id,
                    root_page=root_page,
                    folder_page=current_page.next_number,
                ),
            )
        )
    if navigation:
        builder.row(*navigation)

    builder.row(
        InlineKeyboardButton(
            text="Подписаться на все",
            callback_data=_folder_callback(
                SubscriptionFolderAction.subscribe_all,
                folder_id=folder.folder.id,
                root_page=root_page,
                folder_page=current_page.number,
            ),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Отписаться от всех",
            callback_data=_folder_callback(
                SubscriptionFolderAction.unsubscribe_all,
                folder_id=folder.folder.id,
                root_page=root_page,
                folder_page=current_page.number,
            ),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад",
            callback_data=_folder_callback(
                SubscriptionFolderAction.back,
                folder_id=folder.folder.id,
                root_page=root_page,
                folder_page=current_page.number,
            ),
        )
    )
    return builder.as_markup()


def _add_subscription_button(
    builder: InlineKeyboardBuilder,
    subscription: SubscriptionInfo,
    *,
    folder_id: int,
    root_page: int,
    folder_page: int,
) -> None:
    emoji = Emoji.subscribed if subscription.subscribed else Emoji.unsubscribed
    schedule = format_subscription_schedule(
        schedule_kind=subscription.schedule_kind,
        weekdays=subscription.weekdays,
        annual_date=subscription.annual_date,
    )
    builder.button(
        text=f"{emoji} {subscription.name} – {subscription.send_time.strftime('%H:%M')} · {schedule}",
        callback_data=SubscriptionMenuActionCallbackData(
            category_id=subscription.id,
            subscribe=not subscription.subscribed,
            folder_id=folder_id,
            pages=pack_subscription_pages(root_page=root_page, folder_page=folder_page),
        ),
    )


def _folder_callback(
    action: SubscriptionFolderAction,
    *,
    folder_id: int,
    root_page: int,
    folder_page: int,
) -> str:
    return SubscriptionFolderCallbackData(
        action=action,
        folder_id=folder_id,
        pages=pack_subscription_pages(root_page=root_page, folder_page=folder_page),
    ).pack()


def _subscription_sort_key(subscription: SubscriptionInfo) -> tuple[time, str]:
    return subscription.send_time, subscription.name.casefold()


def _subscription_folder_sort_key(folder: SubscriptionFolderMenu) -> str:
    return folder.folder.name.casefold()


def format_category_button_text(subscription_type: SubscriptionType) -> str:
    return subscription_type.name.capitalize()


def category_button_sort_key(subscription_type: SubscriptionType) -> tuple[bool, time, str]:
    return (
        subscription_type.time is None,
        subscription_type.time or time.min,
        subscription_type.name.casefold() if subscription_type.time is None else "",
    )


def create_reply_keyboard(
    subscription_types: Iterable[SubscriptionType],
    *,
    is_admin: bool = False,
) -> ReplyKeyboardMarkup:
    reply_keyboard_builder = ReplyKeyboardBuilder()

    if is_admin:
        reply_keyboard_builder.button(text="Админ-панель")
    reply_keyboard_builder.button(text="Подписки")

    for subscription_type in sorted(subscription_types, key=category_button_sort_key):
        reply_keyboard_builder.button(text=format_category_button_text(subscription_type))

    reply_keyboard_builder.adjust(*(1, 1, 3) if is_admin else (1, 3))
    return reply_keyboard_builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Выберите категорию",
        selective=False,
    )

from collections.abc import Iterable
from datetime import time

from aiogram.types import (
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from cringe_pics_telebot.bot.emojis import Emoji
from cringe_pics_telebot.bot.subscription_callback_data import SubscriptionCallbackData
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import (
    SubscriptionType,
)
from cringe_pics_telebot.services.subscription_schedules import format_subscription_weekdays


def create_inline_subscriptions_keyboard(
    subscriptions: Iterable[SubscriptionInfo],
) -> InlineKeyboardMarkup:
    inline_keyboard_builder = InlineKeyboardBuilder()

    for subscription in sorted(subscriptions, key=lambda s: s.send_time):
        emoji = Emoji.subscribed if subscription.subscribed else Emoji.unsubscribed
        schedule = format_subscription_weekdays(subscription.weekdays)
        inline_keyboard_builder.button(
            text=f"{emoji} {subscription.name} – {subscription.send_time.strftime('%H:%M')} · {schedule}",
            callback_data=SubscriptionCallbackData(
                category_id=subscription.id,
                subscribe=not subscription.subscribed,
            ),
        )

    inline_keyboard_builder.adjust(1, repeat=True)
    return inline_keyboard_builder.as_markup()


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
        selective=True,
    )

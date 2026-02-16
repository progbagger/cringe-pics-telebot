import json
from collections.abc import Iterable

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from cringe_pics_telebot.bot.utils import Emojis
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import (
    SubscriptionType,
)


def create_inline_subscriptions_keyboard(
    subscriptions: Iterable[SubscriptionInfo],
) -> InlineKeyboardMarkup:
    inline_keyboard_builder = InlineKeyboardBuilder()

    for subscription in subscriptions:
        inline_keyboard_builder.button(
            text=f"{Emojis.subscribed if subscription.subscribed else Emojis.unsubscribed}"  # noqa: E501
            f" {subscription.name}, {subscription.send_time.strftime('HH:MM')}",
            callback_data=json.dumps(
                {
                    "category_id": subscription.id,
                    "subscribe": not subscription.subscribed,
                }
            ),
        )

    inline_keyboard_builder.adjust(1, repeat=True)
    return inline_keyboard_builder.as_markup()


def create_reply_keyboard(
    subscription_types: Iterable[SubscriptionType],
) -> ReplyKeyboardMarkup:
    reply_keyboard_builder = ReplyKeyboardBuilder()

    reply_keyboard_builder.button(text="Подписки")

    for subscription_type in sorted(subscription_types, key=lambda st: st.time):
        reply_keyboard_builder.button(text=subscription_type.name.capitalize())

    return reply_keyboard_builder.as_markup()

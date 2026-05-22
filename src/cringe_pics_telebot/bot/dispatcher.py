import logging
from functools import partial

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from cringe_pics_telebot.bot.keyboards import (
    create_inline_subscriptions_keyboard,
    create_reply_keyboard,
)
from cringe_pics_telebot.bot.subscription_callback_data import SubscriptionCallbackData
from cringe_pics_telebot.bot.utils import check_has_properties
from cringe_pics_telebot.repositories.postgres.connection import (
    transaction,
)
from cringe_pics_telebot.repositories.postgres.entities.subscription import (
    CreateSubscription,
)
from cringe_pics_telebot.repositories.postgres.subscription import (
    create_subscription,
    delete_subscription,
)
from cringe_pics_telebot.repositories.postgres.subscription_types import (
    get_subscription_types,
)
from cringe_pics_telebot.services.subscriptions import get_user_subscriptions

logger = logging.getLogger(__name__)

dp = Dispatcher()

check_with_logger = partial(check_has_properties, logger=logger)

check_from_user = check_with_logger("from_user")
check_has_data = check_with_logger("data")


@dp.message(Command("start", "help"))
@check_from_user
async def handle_start(message: Message) -> None:
    assert message.from_user is not None

    subscription_types = await get_subscription_types()
    text = f"""\
<b>Приветствую, <i>{message.from_user.first_name}</i>!</b>

Я - бот, который будет присылать тебе <b>кринжульки из WhatsApp</b>, \
если ты, конечно, подпишешься на рассылку...
Вот, что я умею:

<code>/list</code> - <i>показать список доступных рассылок. \
Тут же можно будет отписаться/подписаться на них.</i>\

<code>/morning</code> - <i>отправить картинку <b>С добрым утром!</b></i>
<code>/evening</code> - <i>отправить картинку <b>Хорошего вечера!</b></i>
<code>/night</code> - <i>отправить картинку <b>Доброй ночи!</b></i>
<code>/day</code> - <i>отправить картинку в духе <b>С днём дня!</b></i>
<code>/random</code> - <i>отправить случайную картинку из предыдущих категорий</i>\
"""

    await message.answer(
        text=text,
        reply_markup=create_reply_keyboard(subscription_types),
    )


@dp.message(Command("list", "subscriptions"))
@dp.message(F.text.lower() == "подписки")
@check_from_user
async def show_subscriptions(message: Message) -> None:
    assert message.from_user is not None

    subscriptions = await get_user_subscriptions(message.from_user.id)
    await message.answer(
        text="""\
Вот <b>список</b> твоих подписок.

<b>Кликни</b> на подписку, чтобы <b>подписаться/отписаться</b> от рассылки.

<i>Время по Новосибирску (GMT +7, MSK +4)</i>\
""",
        reply_markup=create_inline_subscriptions_keyboard(subscriptions),
    )


@dp.callback_query(SubscriptionCallbackData.filter())
@check_has_data
async def process_subscribtion(callback: CallbackQuery) -> None:
    assert callback.data is not None

    try:
        subscription_params = SubscriptionCallbackData.unpack(callback.data)
        async with transaction():
            if subscription_params.subscribe:
                await create_subscription(
                    CreateSubscription(
                        subscription_type_id=subscription_params.category_id,
                        user_id=callback.from_user.id,
                    )
                )
                logger.info(
                    "User %d subscribed to category %d",
                    callback.from_user.id,
                    subscription_params.category_id,
                )
                await callback.answer("Подписка оформлена!")

            else:
                await delete_subscription(
                    user_id=callback.from_user.id,
                    subscription_type_id=subscription_params.category_id,
                )
                logger.info(
                    "User %d unsubscribed from category %d",
                    callback.from_user.id,
                    subscription_params.category_id,
                )
                await callback.answer("Подписка удалена!")

            if callback.message is not None and not isinstance(
                callback.message,
                InaccessibleMessage,
            ):
                updated_user_subscriptions = await get_user_subscriptions(
                    callback.from_user.id
                )
                await callback.message.edit_reply_markup(
                    reply_markup=create_inline_subscriptions_keyboard(
                        updated_user_subscriptions
                    )
                )

            else:
                logger.error(
                    "Message is not accessible for user %d in callback %d",
                    callback.from_user.id,
                    callback.id,
                )
    except Exception:
        logger.exception(
            "Failed to subscribe user %d to category %d",
            callback.from_user.id,
            subscription_params.category_id,
        )

        if not await callback.answer("Что-то пошло не так...", show_alert=True):
            logger.error("Failed to show alert to user %d", callback.from_user.id)

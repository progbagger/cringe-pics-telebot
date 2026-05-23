import logging
from functools import partial

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InaccessibleMessage,
    InputMediaAnimation,
    InputMediaPhoto,
    Message,
)

from cringe_pics_telebot.bot.keyboards import (
    create_inline_subscriptions_keyboard,
    create_reply_keyboard,
)
from cringe_pics_telebot.bot.subscription_callback_data import SubscriptionCallbackData
from cringe_pics_telebot.bot.utils import check_has_properties
from cringe_pics_telebot.repositories.postgres.connection import (
    transaction,
)
from cringe_pics_telebot.services.random_image import get_random_image
from cringe_pics_telebot.services.subscriptions import (
    get_subscription_types,
    get_user_subscriptions,
    subscribe,
    unsubscribe,
)

logger = logging.getLogger(__name__)

dp = Dispatcher()

check_with_logger = partial(check_has_properties, logger=logger)

check_from_user = check_with_logger("from_user")
check_has_data = check_with_logger("data")
check_has_text = check_with_logger("text")


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
                await subscribe(user_id=callback.from_user.id, subscription_type_id=subscription_params.category_id)
                logger.info(
                    "User %d subscribed to category %d",
                    callback.from_user.id,
                    subscription_params.category_id,
                )
                await callback.answer("Подписка оформлена!")

            else:
                await unsubscribe(
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
                updated_user_subscriptions = await get_user_subscriptions(callback.from_user.id)
                await callback.message.edit_reply_markup(
                    reply_markup=create_inline_subscriptions_keyboard(updated_user_subscriptions)
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


@dp.message(F.text.is_not(None))
@check_has_text
@check_from_user
async def send_image(message: Message) -> None:
    assert message.text is not None
    assert message.from_user is not None

    sent_message = await message.reply("<i>Выбираю картинку...</i>")

    try:
        subscription_types_by_name = {st.name.lower(): st for st in await get_subscription_types()}
        if s := subscription_types_by_name.get(message.text.lower()):
            image = await get_random_image(s.id)

            if "gif" in image.mime_type:
                await sent_message.edit_media(
                    InputMediaAnimation(media=BufferedInputFile(image.data, f"{s.s3_directory_path}.gif"))
                )
            else:
                await sent_message.edit_media(InputMediaPhoto(media=BufferedInputFile(image.data, s.s3_directory_path)))
    except Exception:
        logger.exception("Failed to send media to user %d", message.from_user.id)
        await sent_message.edit_text("Произошла ошибка. Попробуй ещё раз.")

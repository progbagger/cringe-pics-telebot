import logging

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
from cringe_pics_telebot.repositories.postgres.connection import (
    transaction,
)
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import SubscriptionType
from cringe_pics_telebot.services.random_image import get_random_image
from cringe_pics_telebot.services.subscriptions import (
    get_subscription_types,
    get_user_subscriptions,
    subscribe,
    unsubscribe,
)

logger = logging.getLogger(__name__)

dp = Dispatcher()


@dp.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    if message.from_user is None:
        logger.info("Received message without from_user: %d", message.message_id)
        return

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
@dp.message(F.text.lower().contains("подписк"))
async def show_subscriptions(message: Message) -> None:
    if message.from_user is None:
        logger.info("Received message without from_user: %d", message.message_id)
        return

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
async def process_subscribtion(callback: CallbackQuery) -> None:
    if callback.data is None:
        logger.error("Received callback query without data: %d", callback.id)
        return

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


async def _subscription_type_filter(message: Message) -> dict[str, SubscriptionType] | bool:
    if message.text is not None:
        subscription_types_by_name = {st.name.lower(): st for st in await get_subscription_types()}
        if s := subscription_types_by_name.get(message.text.lower()):
            return {"subscription_type": s}

    return False


@dp.message(_subscription_type_filter)
async def send_image(message: Message, *, subscription_type: SubscriptionType) -> None:
    if message.text is None or message.from_user is None:
        logger.info("Received message without text or from_user: %d", message.message_id)
        return

    sent_message = await message.reply("<i>Выбираю картинку</i>")

    try:
        image = await get_random_image(subscription_type.id)
        if "gif" in image.mime_type:
            filename = f"{subscription_type.s3_directory_path}.gif"
            input_media_type = InputMediaAnimation
        else:
            filename = subscription_type.s3_directory_path
            input_media_type = InputMediaPhoto
        await sent_message.edit_media(input_media_type(media=BufferedInputFile(image.data, filename)))

    except Exception:
        logger.exception("Failed to send media to user %d", message.from_user.id)
        await sent_message.edit_text("<b>Произошла непредвиденная ошибка.</b>")


@dp.message()
async def unknown_message(message: Message) -> None:
    await handle_start(message)

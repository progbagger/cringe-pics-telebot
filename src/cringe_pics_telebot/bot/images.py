import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    Message,
)

from cringe_pics_telebot.bot.keyboards import (
    create_inline_subscriptions_keyboard,
    create_reply_keyboard,
)
from cringe_pics_telebot.bot.media import add_image_to_message
from cringe_pics_telebot.bot.subscription_callback_data import SubscriptionCallbackData
from cringe_pics_telebot.repositories.postgres import SubscriptionType, is_administrator
from cringe_pics_telebot.repositories.postgres.connection import (
    transaction,
)
from cringe_pics_telebot.services.media_delivery import deliver_category_media
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia, get_random_image
from cringe_pics_telebot.services.subscriptions import (
    get_subscription_types,
    get_user_subscriptions,
    subscribe,
    unsubscribe,
)
from cringe_pics_telebot.services.timezones import (
    InvalidTimezoneOffsetError,
    format_timezone_offset,
    get_user_timezone_offset,
    parse_timezone_offset,
    set_user_timezone_offset,
)

logger = logging.getLogger(__name__)

router = Router(name="main")


@router.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    if message.from_user is None:
        logger.info("Received message without from_user: %d", message.message_id)
        return

    subscription_types = sorted(
        await get_subscription_types() or [],
        key=lambda subscription_type: subscription_type.time,
    )
    category_commands = ", ".join(
        f"<code>{escape(subscription_type.name)}</code>" for subscription_type in subscription_types
    )
    timezone_offset = format_timezone_offset(await get_user_timezone_offset(message.from_user.id))
    text = f"""\
<b>Привет, {escape(message.from_user.first_name)}!</b>

<b>Что умеет бот</b>
Присылаю кринжовые картинки из WhatsApp — по запросу или по расписанию.

<b>🖼 Получить картинку сейчас</b>
Нажми кнопку с категорией или отправь одну из команд:
{category_commands}

<b>🔔 Настроить рассылки</b>
<code>/list</code> или <code>/subscriptions</code> — подписаться на категорию или отписаться \
от неё. Время каждой категории применяется в твоём часовом поясе: UTC{timezone_offset}.
<code>/timezone [+HH:MM]</code> — посмотреть или изменить часовой пояс.

<b>💬 Отправить картинку в другой чат</b>
Введи <code>@имя_бота</code> и название категории без <code>/</code>, затем выбери картинку. \
Первый результат 🎲 отправит случайную.
"""

    await message.answer(
        text=text,
        reply_markup=create_reply_keyboard(
            subscription_types,
            is_admin=await is_administrator(message.from_user.id),
        ),
    )


@router.message(Command("timezone"))
async def handle_timezone(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        logger.info("Received timezone command without from_user: %d", message.message_id)
        return

    if command.args is None:
        timezone_offset = format_timezone_offset(await get_user_timezone_offset(message.from_user.id))
        await message.answer(
            f"Твой текущий часовой пояс: <b>UTC{timezone_offset}</b>.\n\n"
            "Чтобы изменить его, отправь команду, например: <code>/timezone +04:00</code>."
        )
        return

    try:
        offset_minutes = parse_timezone_offset(command.args)
    except InvalidTimezoneOffsetError:
        await message.answer(
            "Не удалось распознать часовой пояс. Укажи фиксированное смещение от "
            "<code>-12:00</code> до <code>+14:00</code> в формате <code>±HH:MM</code>, "
            "например <code>/timezone +04:00</code>."
        )
        return

    await set_user_timezone_offset(
        user_id=message.from_user.id,
        offset_minutes=offset_minutes,
    )
    await message.answer(
        f"Часовой пояс сохранён: <b>UTC{format_timezone_offset(offset_minutes)}</b>. "
        "Время всех категорий теперь применяется в этом часовом поясе."
    )


@router.message(Command("list", "subscriptions"))
@router.message(F.text.lower().contains("подписк"))
async def show_subscriptions(message: Message) -> None:
    if message.from_user is None:
        logger.info("Received message without from_user: %d", message.message_id)
        return

    subscriptions = await get_user_subscriptions(message.from_user.id)
    timezone_offset = format_timezone_offset(await get_user_timezone_offset(message.from_user.id))
    await message.answer(
        text=f"""\
Вот <b>список</b> твоих подписок.

<b>Кликни</b> на подписку, чтобы <b>подписаться/отписаться</b> от рассылки.

<i>Время категорий — локальное, твой часовой пояс: UTC{timezone_offset}.</i>
<i>Изменить его можно командой <code>/timezone</code>.</i>\
""",
        reply_markup=create_inline_subscriptions_keyboard(subscriptions),
    )


@router.callback_query(SubscriptionCallbackData.filter())
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
        subscription_types_by_name = {st.name.lower(): st for st in await get_subscription_types() or []}
        if s := subscription_types_by_name.get(message.text.lower()):
            return {"subscription_type": s}

    return False


@router.message(_subscription_type_filter)
async def send_image(message: Message, *, subscription_type: SubscriptionType) -> None:
    if message.text is None or message.from_user is None:
        logger.info("Received message without text or from_user: %d", message.message_id)
        return

    sent_message = await message.reply("<i>Выбираю картинку</i>")

    try:
        media = await get_random_image(subscription_type.id)
        await deliver_category_media(
            media,
            send=lambda image: _add_image_to_chat_message(message=sent_message, image=image),
        )

    except Exception:
        logger.exception("Failed to send media to user %d", message.from_user.id)
        await sent_message.edit_text("<b>Произошла непредвиденная ошибка.</b>")
        return


@router.message()
async def unknown_message(message: Message) -> None:
    await handle_start(message)


async def _add_image_to_chat_message(*, message: Message, image: LinkedMedia | CachedMedia) -> Message:
    result = await add_image_to_message(message=message, image=image)

    assert isinstance(result, Message)
    return result

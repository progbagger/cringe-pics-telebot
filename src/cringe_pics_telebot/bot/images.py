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
    category_button_sort_key,
    create_inline_subscriptions_keyboard,
)
from cringe_pics_telebot.bot.media import send_image_to_chat
from cringe_pics_telebot.bot.subscription_callback_data import (
    SubscriptionActionCallbackData,
    SubscriptionCallbackData,
    SubscriptionPageCallbackData,
)
from cringe_pics_telebot.entities.subscription_schedule import SubscriptionScheduleKind
from cringe_pics_telebot.entities.user_birthday import UserBirthdaySource
from cringe_pics_telebot.repositories.postgres import (
    SubscriptionType,
    get_category_media_by_subscription_types,
)
from cringe_pics_telebot.services.birthdays import (
    InvalidBirthdayError,
    clear_birthday,
    get_user_birthday,
    parse_birthday_command,
    set_manual_birthday,
)
from cringe_pics_telebot.services.subscriptions import (
    SubscriptionTypeUnavailableError,
    get_subscription_types,
    get_user_subscriptions,
    subscribe,
    unsubscribe,
    user_has_birthday,
)
from cringe_pics_telebot.services.timezones import (
    InvalidTimezoneOffsetError,
    format_timezone_offset,
    get_user_timezone_offset,
    parse_timezone_offset,
    set_user_timezone_offset,
)
from cringe_pics_telebot.services.user_media_cycles import deliver_user_category_media

logger = logging.getLogger(__name__)

router = Router(name="main")


@router.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    if message.from_user is None:
        logger.info("Received message without from_user: %d", message.message_id)
        return

    subscription_types = sorted(
        await get_subscription_types() or [],
        key=category_button_sort_key,
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
<code>/birthday [DD.MM|clear]</code> — посмотреть, вручную указать или удалить день рождения. \
Бот хранит только день и месяц; ручной ввод добровольный и имеет приоритет над данными профиля Telegram.

<b>💬 Отправить картинку в другой чат</b>
Введи <code>@имя_бота</code> и название категории без <code>/</code>, затем выбери картинку. \
Первый результат 🎲 отправит случайную.
"""

    await message.answer(text=text)


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


@router.message(Command("birthday"))
async def handle_birthday(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        logger.info("Received birthday command without from_user: %d", message.message_id)
        return

    user_id = message.from_user.id
    if command.args is None or not command.args.strip():
        current_birthday = await get_user_birthday(user_id)
        if current_birthday is None:
            await message.answer(
                "Твой день рождения пока не указан. Чтобы сохранить только день и месяц вручную, "
                "отправь, например: <code>/birthday 31.12</code>."
            )
            return

        source = "указан вручную" if current_birthday.source is UserBirthdaySource.manual else "получен из Telegram"
        await message.answer(f"Твой день рождения: <b>{current_birthday.date.format()}</b> — {source}.")
        return

    try:
        parsed_birthday = parse_birthday_command(command.args)
    except InvalidBirthdayError:
        await message.answer(
            "Не удалось распознать дату. Используй формат <code>DD.MM</code>, например "
            "<code>/birthday 31.12</code>, или команду <code>/birthday clear</code>."
        )
        return

    if parsed_birthday is None:
        await clear_birthday(user_id)
        await message.answer(
            "День рождения удалён. Следующая синхронизация сможет снова получить его из профиля Telegram."
        )
        return

    await set_manual_birthday(user_id=user_id, birthday=parsed_birthday)
    await message.answer(
        f"День рождения сохранён: <b>{parsed_birthday.format()}</b>. "
        "Ручное значение имеет приоритет над значением из профиля Telegram."
    )


@router.message(Command("list", "subscriptions"))
@router.message(F.text.lower().contains("подписк"))
async def show_subscriptions(message: Message) -> None:
    if message.from_user is None:
        logger.info("Received message without from_user: %d", message.message_id)
        return

    subscriptions = await get_user_subscriptions(message.from_user.id)
    timezone_offset = format_timezone_offset(await get_user_timezone_offset(message.from_user.id))
    birthday_hint = ""
    if any(
        item.schedule_kind is SubscriptionScheduleKind.annual_birthday for item in subscriptions
    ) and not await user_has_birthday(message.from_user.id):
        birthday_hint = "\n<i>Для рассылки в день рождения укажи дату командой <code>/birthday DD.MM</code>.</i>\n"
    await message.answer(
        text=f"""\
Вот <b>список</b> твоих подписок.

<b>Кликни</b> на подписку, чтобы <b>подписаться/отписаться</b> от рассылки.

<i>Время категорий — локальное, твой часовой пояс: UTC{timezone_offset}.</i>
<i>Изменить его можно командой <code>/timezone</code>.</i>\
{birthday_hint}\
""",
        reply_markup=create_inline_subscriptions_keyboard(subscriptions),
    )


@router.callback_query(SubscriptionPageCallbackData.filter())
async def paginate_subscriptions(callback: CallbackQuery) -> None:
    if callback.data is None:
        logger.error("Received subscription page callback without data: %d", callback.id)
        return

    try:
        page = SubscriptionPageCallbackData.unpack(callback.data).page
        if callback.message is None or isinstance(callback.message, InaccessibleMessage):
            logger.error("Subscription list message is not accessible for callback %d", callback.id)
            await callback.answer("Список подписок недоступен.", show_alert=True)
            return

        subscriptions = await get_user_subscriptions(callback.from_user.id)
        await callback.message.edit_reply_markup(
            reply_markup=create_inline_subscriptions_keyboard(subscriptions, page=page)
        )
        await callback.answer()
    except Exception:
        logger.exception("Failed to change subscription page for user %d", callback.from_user.id)
        if not await callback.answer("Что-то пошло не так...", show_alert=True):
            logger.error("Failed to show alert to user %d", callback.from_user.id)


@router.callback_query(SubscriptionActionCallbackData.filter())
@router.callback_query(SubscriptionCallbackData.filter())
async def process_subscription(callback: CallbackQuery) -> None:
    if callback.data is None:
        logger.error("Received callback query without data: %d", callback.id)
        return

    subscription_category_id: int | None = None
    try:
        if callback.data.startswith("subscription_action:"):
            action_params = SubscriptionActionCallbackData.unpack(callback.data)
            page = action_params.page
        else:
            legacy_params = SubscriptionCallbackData.unpack(callback.data)
            action_params = SubscriptionActionCallbackData(
                category_id=legacy_params.category_id,
                subscribe=legacy_params.subscribe,
                page=0,
            )
            page = 0
        subscription_category_id = action_params.category_id
        if action_params.subscribe:
            try:
                await subscribe(user_id=callback.from_user.id, subscription_type_id=action_params.category_id)
            except SubscriptionTypeUnavailableError:
                logger.info(
                    "User %d tried to subscribe to unavailable category %d",
                    callback.from_user.id,
                    action_params.category_id,
                )
                await _refresh_subscription_keyboard(callback, page=page)
                await callback.answer("Категория больше недоступна.", show_alert=True)
                return

            logger.info(
                "User %d subscribed to category %d",
                callback.from_user.id,
                action_params.category_id,
            )
            await callback.answer("Подписка оформлена!")
        else:
            await unsubscribe(
                user_id=callback.from_user.id,
                subscription_type_id=action_params.category_id,
            )
            logger.info(
                "User %d unsubscribed from category %d",
                callback.from_user.id,
                action_params.category_id,
            )
            await callback.answer("Подписка удалена!")

        if callback.message is not None and not isinstance(
            callback.message,
            InaccessibleMessage,
        ):
            await _refresh_subscription_keyboard(callback, page=page)
        else:
            logger.error(
                "Message is not accessible for user %d in callback %d",
                callback.from_user.id,
                callback.id,
            )
    except Exception:
        logger.exception(
            "Failed to update subscription for user %d and category %s",
            callback.from_user.id,
            subscription_category_id,
        )

        if not await callback.answer("Что-то пошло не так...", show_alert=True):
            logger.error("Failed to show alert to user %d", callback.from_user.id)


async def _refresh_subscription_keyboard(callback: CallbackQuery, *, page: int) -> None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return
    subscriptions = await get_user_subscriptions(callback.from_user.id)
    await callback.message.edit_reply_markup(
        reply_markup=create_inline_subscriptions_keyboard(subscriptions, page=page)
    )


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

    try:
        bot = message.bot
        if bot is None:
            raise RuntimeError("Message is not bound to a bot")

        media = await get_category_media_by_subscription_types([subscription_type.id])
        await deliver_user_category_media(
            user_id=message.from_user.id,
            subscription_type_id=subscription_type.id,
            media=media,
            send=lambda image: send_image_to_chat(bot=bot, chat_id=message.chat.id, image=image),
        )

    except Exception:
        logger.exception("Failed to send media to user %d", message.from_user.id)

        try:
            await message.answer("<b>Произошла непредвиденная ошибка.</b>")
        except Exception:
            logger.exception("Failed to notify user %d about media delivery failure", message.from_user.id)


@router.message()
async def unknown_message(message: Message) -> None:
    await handle_start(message)

import logging
from dataclasses import dataclass
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
    create_inline_subscription_folder_keyboard,
    create_inline_subscriptions_keyboard,
)
from cringe_pics_telebot.bot.main_keyboard import without_main_keyboard
from cringe_pics_telebot.bot.media import MediaDeliveryReceipt, add_image_to_message
from cringe_pics_telebot.bot.subscription_callback_data import (
    SubscriptionActionCallbackData,
    SubscriptionCallbackData,
    SubscriptionFolderAction,
    SubscriptionFolderCallbackData,
    SubscriptionMenuActionCallbackData,
    SubscriptionPageCallbackData,
    unpack_subscription_pages,
)
from cringe_pics_telebot.entities.subscription_menu import (
    SubscriptionFolderMenu,
    UserSubscriptionMenu,
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
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia
from cringe_pics_telebot.services.subscriptions import (
    SubscriptionFolderUnavailableError,
    SubscriptionMenuLocationUnavailableError,
    SubscriptionTypeUnavailableError,
    get_subscription_types,
    get_user_subscription_menu,
    set_folder_subscriptions,
    subscribe,
    subscribe_from_menu,
    unsubscribe,
    unsubscribe_from_menu,
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


@dataclass(frozen=True, slots=True)
class _SubscriptionCallbackParams:
    category_id: int
    should_subscribe: bool
    validate_location: bool
    folder_id: int | None
    root_page: int
    folder_page: int


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

    menu = await get_user_subscription_menu(message.from_user.id)
    await message.answer(
        text=await _subscription_list_text(message.from_user.id, menu),
        reply_markup=create_inline_subscriptions_keyboard(menu),
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

        menu = await get_user_subscription_menu(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=create_inline_subscriptions_keyboard(menu, page=page))
        await callback.answer()
    except Exception:
        logger.exception("Failed to change subscription page for user %d", callback.from_user.id)
        if not await callback.answer("Что-то пошло не так...", show_alert=True):
            logger.error("Failed to show alert to user %d", callback.from_user.id)


@router.callback_query(SubscriptionFolderCallbackData.filter())
async def process_subscription_folder(callback: CallbackQuery) -> None:
    if callback.data is None:
        logger.error("Received subscription folder callback without data: %d", callback.id)
        return

    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        logger.error("Subscription folder message is not accessible for callback %d", callback.id)
        await callback.answer("Список подписок недоступен.", show_alert=True)
        return

    try:
        params = SubscriptionFolderCallbackData.unpack(callback.data)
        root_page, folder_page = unpack_subscription_pages(params.pages)
        if params.action is SubscriptionFolderAction.back:
            await _edit_subscription_root(callback, root_page=root_page)
            await callback.answer()
            return

        menu = await get_user_subscription_menu(callback.from_user.id)
        folder = menu.find_folder(params.folder_id)
        if folder is None:
            await _edit_subscription_root(callback, root_page=root_page, menu=menu)
            await callback.answer("Папка больше недоступна.", show_alert=True)
            return

        if params.action in {
            SubscriptionFolderAction.subscribe_all,
            SubscriptionFolderAction.unsubscribe_all,
        }:
            subscribe_to_all = params.action is SubscriptionFolderAction.subscribe_all
            try:
                await set_folder_subscriptions(
                    user_id=callback.from_user.id,
                    folder_id=params.folder_id,
                    subscribe=subscribe_to_all,
                )
            except SubscriptionFolderUnavailableError:
                await _edit_subscription_root(callback, root_page=root_page)
                await callback.answer("Папка больше недоступна.", show_alert=True)
                return

            await _refresh_subscription_location(
                callback,
                folder_id=params.folder_id,
                root_page=root_page,
                folder_page=folder_page,
            )
            await callback.answer("Подписки оформлены!" if subscribe_to_all else "Подписки удалены!")
            return

        markup = create_inline_subscription_folder_keyboard(
            folder,
            root_page=root_page,
            folder_page=folder_page,
        )
        if params.action is SubscriptionFolderAction.open:
            await callback.message.edit_text(
                _subscription_folder_text(folder),
                reply_markup=markup,
            )
        else:
            await callback.message.edit_reply_markup(reply_markup=markup)
        await callback.answer()
    except Exception:
        logger.exception("Failed to process subscription folder for user %d", callback.from_user.id)
        if not await callback.answer("Что-то пошло не так...", show_alert=True):
            logger.error("Failed to show alert to user %d", callback.from_user.id)


@router.callback_query(SubscriptionMenuActionCallbackData.filter())
@router.callback_query(SubscriptionActionCallbackData.filter())
@router.callback_query(SubscriptionCallbackData.filter())
async def process_subscription(callback: CallbackQuery) -> None:
    if callback.data is None:
        logger.error("Received callback query without data: %d", callback.id)
        return

    try:
        await _process_subscription_callback(callback, callback.data)
    except Exception:
        logger.exception("Failed to update subscription for user %d", callback.from_user.id)

        if not await callback.answer("Что-то пошло не так...", show_alert=True):
            logger.error("Failed to show alert to user %d", callback.from_user.id)


async def _process_subscription_callback(callback: CallbackQuery, data: str) -> None:
    params = _unpack_subscription_callback(data)
    try:
        await _update_subscription_from_callback(
            user_id=callback.from_user.id,
            params=params,
        )
    except SubscriptionMenuLocationUnavailableError, SubscriptionTypeUnavailableError:
        logger.info(
            "User %d tried to update unavailable category %d",
            callback.from_user.id,
            params.category_id,
        )
        await _refresh_subscription_location(
            callback,
            folder_id=params.folder_id,
            root_page=params.root_page,
            folder_page=params.folder_page,
        )
        await callback.answer("Категория больше недоступна.", show_alert=True)
        return

    logger.info(
        "User %d %s category %d",
        callback.from_user.id,
        "subscribed to" if params.should_subscribe else "unsubscribed from",
        params.category_id,
    )
    await callback.answer("Подписка оформлена!" if params.should_subscribe else "Подписка удалена!")

    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        logger.error(
            "Message is not accessible for user %d in callback %d",
            callback.from_user.id,
            callback.id,
        )
        return

    await _refresh_subscription_location(
        callback,
        folder_id=params.folder_id,
        root_page=params.root_page,
        folder_page=params.folder_page,
    )


def _unpack_subscription_callback(data: str) -> _SubscriptionCallbackParams:
    if data.startswith("s:"):
        menu_params = SubscriptionMenuActionCallbackData.unpack(data)
        root_page, folder_page = unpack_subscription_pages(menu_params.pages)
        return _SubscriptionCallbackParams(
            category_id=menu_params.category_id,
            should_subscribe=menu_params.subscribe,
            validate_location=True,
            folder_id=menu_params.folder_id or None,
            root_page=root_page,
            folder_page=folder_page,
        )

    if data.startswith("subscription_action:"):
        action_params = SubscriptionActionCallbackData.unpack(data)
        return _SubscriptionCallbackParams(
            category_id=action_params.category_id,
            should_subscribe=action_params.subscribe,
            validate_location=False,
            folder_id=None,
            root_page=action_params.page,
            folder_page=0,
        )

    legacy_params = SubscriptionCallbackData.unpack(data)
    return _SubscriptionCallbackParams(
        category_id=legacy_params.category_id,
        should_subscribe=legacy_params.subscribe,
        validate_location=False,
        folder_id=None,
        root_page=0,
        folder_page=0,
    )


async def _update_subscription_from_callback(*, user_id: int, params: _SubscriptionCallbackParams) -> None:
    if params.should_subscribe:
        if params.validate_location:
            await subscribe_from_menu(
                user_id=user_id,
                subscription_type_id=params.category_id,
                folder_id=params.folder_id,
            )
            return

        await subscribe(user_id=user_id, subscription_type_id=params.category_id)
        return

    if params.validate_location:
        await unsubscribe_from_menu(
            user_id=user_id,
            subscription_type_id=params.category_id,
            folder_id=params.folder_id,
        )
        return

    await unsubscribe(user_id=user_id, subscription_type_id=params.category_id)


async def _refresh_subscription_location(
    callback: CallbackQuery,
    *,
    folder_id: int | None,
    root_page: int,
    folder_page: int,
) -> None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return

    menu = await get_user_subscription_menu(callback.from_user.id)
    if folder_id is None:
        await callback.message.edit_reply_markup(
            reply_markup=create_inline_subscriptions_keyboard(menu, page=root_page)
        )
        return

    folder = menu.find_folder(folder_id)
    if folder is None:
        await _edit_subscription_root(callback, root_page=root_page, menu=menu)
        return

    await callback.message.edit_reply_markup(
        reply_markup=create_inline_subscription_folder_keyboard(
            folder,
            root_page=root_page,
            folder_page=folder_page,
        )
    )


async def _edit_subscription_root(
    callback: CallbackQuery,
    *,
    root_page: int,
    menu: UserSubscriptionMenu | None = None,
) -> None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return

    current_menu = menu or await get_user_subscription_menu(callback.from_user.id)
    await callback.message.edit_text(
        await _subscription_list_text(callback.from_user.id, current_menu),
        reply_markup=create_inline_subscriptions_keyboard(current_menu, page=root_page),
    )


async def _subscription_list_text(user_id: int, menu: UserSubscriptionMenu) -> str:
    timezone_offset = format_timezone_offset(await get_user_timezone_offset(user_id))
    birthday_hint = ""
    if any(
        item.schedule_kind is SubscriptionScheduleKind.annual_birthday for item in menu.subscriptions
    ) and not await user_has_birthday(user_id):
        birthday_hint = "\n<i>Для рассылки в день рождения укажи дату командой <code>/birthday DD.MM</code>.</i>\n"

    return f"""\
Вот <b>список</b> твоих подписок.

<b>Кликни</b> на подписку, чтобы <b>подписаться/отписаться</b> от рассылки.

<i>Время категорий — локальное, твой часовой пояс: UTC{timezone_offset}.</i>
<i>Изменить его можно командой <code>/timezone</code>.</i>\
{birthday_hint}\
"""


def _subscription_folder_text(folder: SubscriptionFolderMenu) -> str:
    return (
        f"<b>Папка «{escape(folder.folder.name)}»</b>\n\n"
        "Кликни на категорию, чтобы подписаться или отписаться от рассылки. "
        "Кнопки внизу изменят все подписки в этой папке."
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

    with without_main_keyboard():
        sent_message = await message.reply("<i>Выбираю картинку</i>")

    try:
        media = await get_category_media_by_subscription_types([subscription_type.id])
        await deliver_user_category_media(
            user_id=message.from_user.id,
            subscription_type_id=subscription_type.id,
            media=media,
            send=lambda image: _add_image_to_chat_message(message=sent_message, image=image),
        )

    except Exception:
        logger.exception("Failed to send media to user %d", message.from_user.id)

        try:
            await sent_message.edit_text("<b>Произошла непредвиденная ошибка.</b>")
        except Exception:
            logger.exception("Failed to replace media placeholder for user %d", message.from_user.id)

            try:
                await message.answer("<b>Произошла непредвиденная ошибка.</b>")
            except Exception:
                logger.exception("Failed to notify user %d about media delivery failure", message.from_user.id)

        return


@router.message()
async def unknown_message(message: Message) -> None:
    await handle_start(message)


async def _add_image_to_chat_message(
    *,
    message: Message,
    image: LinkedMedia | CachedMedia,
) -> MediaDeliveryReceipt:
    return await add_image_to_message(message=message, image=image)

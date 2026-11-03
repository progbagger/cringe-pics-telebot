import logging
from collections.abc import Sequence
from dataclasses import dataclass

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardMarkup, Message

from cringe_pics_telebot.repositories.postgres import CategoryMediaSearchMetadata
from cringe_pics_telebot.services.admin_media import (
    AdminMediaPreview,
    get_admin_media,
    get_admin_media_page,
    prepare_admin_media_preview,
    update_admin_media_search_aliases,
)
from cringe_pics_telebot.services.media_search_aliases import (
    InvalidMediaSearchAliasesError,
    parse_media_search_aliases,
)

from .admin_access import IsAdministrator
from .admin_keyboards import (
    create_admin_media_form_cancel_keyboard,
    create_admin_media_keyboard,
    create_admin_media_list_keyboard,
    create_admin_panel_keyboard,
)
from .admin_media_callback_data import AdminMediaAction, AdminMediaCallbackData
from .admin_media_content import create_admin_media_content

logger = logging.getLogger(__name__)

router = Router(name="admin_media")
router.message.filter(IsAdministrator())
router.callback_query.filter(IsAdministrator())


class AdminMediaAliasesForm(StatesGroup):
    aliases = State()


@dataclass(frozen=True, slots=True)
class _AdminMediaLocation:
    category_id: int
    media_id: int
    category_page: int
    media_page: int
    panel_message_id: int


@router.callback_query(AdminMediaCallbackData.filter())
async def handle_admin_media_callback(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    if message is None or callback.data is None:
        await callback.answer("Сообщение панели недоступно.", show_alert=True)
        return

    callback_data = AdminMediaCallbackData.unpack(callback.data)

    try:
        notice = await _dispatch_admin_media_callback(callback_data, message=message, state=state)
    except Exception:
        logger.exception("Failed to process admin media callback %s", callback.data)
        await callback.answer("Не удалось выполнить действие.", show_alert=True)
        return

    await callback.answer(notice, show_alert=notice is not None)


@router.message(AdminMediaAliasesForm.aliases)
async def receive_admin_media_aliases(message: Message, state: FSMContext) -> None:
    location = await _state_location(state)
    if location is None:
        await message.answer(
            "Черновик потерян. Откройте медиа заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return

    if message.text is None:
        await message.answer(_aliases_error("Алиасы должны быть текстом."))
        return

    try:
        aliases = parse_media_search_aliases(message.text)
    except InvalidMediaSearchAliasesError:
        await message.answer(_aliases_error("Не найдено ни одного непустого алиаса."))
        return

    metadata = await update_admin_media_search_aliases(
        location.category_id,
        location.media_id,
        aliases,
    )
    await state.clear()

    try:
        if metadata is None:
            await _show_media_list(message, location)
            await message.answer("Медиа больше недоступно.")
            return

        await _edit_media_content(
            message,
            location,
            f"Алиасы медиа обновлены.\n\n{_media_details(metadata)}",
            (await prepare_admin_media_preview(metadata),),
            _media_keyboard(metadata, location),
        )
    except Exception as error:
        logger.warning("Admin media panel update failed after alias input: error=%s", type(error).__name__)
        result = "Алиасы медиа сохранены." if metadata is not None else "Медиа больше недоступно."
        await message.answer(
            f"{result} Не удалось обновить сообщение панели. Откройте медиа заново через /admin.",
            reply_markup=create_admin_panel_keyboard(),
        )


async def _dispatch_admin_media_callback(
    callback_data: AdminMediaCallbackData,
    *,
    message: Message,
    state: FSMContext,
) -> str | None:
    location = _location_from_callback(callback_data, message_id=message.message_id)

    match callback_data.action:
        case AdminMediaAction.media_list:
            await state.clear()
            return await _show_media_list(message, location)
        case AdminMediaAction.media:
            await state.clear()
            return await _show_media(message, location)
        case AdminMediaAction.edit_aliases:
            return await _start_edit_aliases(message, state, location)
        case AdminMediaAction.clear_aliases:
            await state.clear()
            return await _clear_aliases(message, location)
        case AdminMediaAction.cancel_form:
            await state.clear()
            notice = await _show_media(message, location)
            return notice or "Редактирование алиасов отменено."

    return None


async def _show_media_list(message: Message, location: _AdminMediaLocation) -> str | None:
    catalog = await get_admin_media_page(location.category_id, location.media_page)
    if catalog is None:
        await _edit_media_content(
            message,
            location,
            "Админ-панель\n\nКатегория больше недоступна.",
            (),
            create_admin_panel_keyboard(),
        )
        return "Категория больше недоступна."

    return await _edit_media_content(
        message,
        location,
        f"Медиа категории {catalog.category.name}\n\n"
        f"Активных элементов: {catalog.total}. Страница {catalog.page.number + 1}.\n"
        "Выберите номер медиа. 🏷 — количество алиасов; 0 — алиасов нет."
        if catalog.total
        else f"Медиа категории {catalog.category.name}\n\nАктивных медиа пока нет.",
        catalog.page.items,
        create_admin_media_list_keyboard(
            catalog,
            category_id=location.category_id,
            category_page=location.category_page,
        ),
        numbered=True,
    )


async def _show_media(message: Message, location: _AdminMediaLocation) -> str | None:
    metadata = await get_admin_media(location.category_id, location.media_id)
    if metadata is None:
        await _show_media_list(message, location)
        return "Медиа больше недоступно."

    return await _edit_media_content(
        message,
        location,
        _media_details(metadata),
        (await prepare_admin_media_preview(metadata),),
        _media_keyboard(metadata, location),
    )


async def _start_edit_aliases(
    message: Message,
    state: FSMContext,
    location: _AdminMediaLocation,
) -> str | None:
    metadata = await get_admin_media(location.category_id, location.media_id)
    if metadata is None:
        await state.clear()
        await _show_media_list(message, location)
        return "Медиа больше недоступно."

    notice = await _edit_media_content(
        message,
        location,
        _aliases_prompt(metadata.media.name),
        (await prepare_admin_media_preview(metadata),),
        create_admin_media_form_cancel_keyboard(
            category_id=location.category_id,
            media_id=location.media_id,
            category_page=location.category_page,
            media_page=location.media_page,
        ),
    )

    await state.set_state(AdminMediaAliasesForm.aliases)
    await state.set_data(
        {
            "category_id": location.category_id,
            "media_id": location.media_id,
            "category_page": location.category_page,
            "media_page": location.media_page,
            "panel_message_id": location.panel_message_id,
        }
    )

    return notice


async def _clear_aliases(message: Message, location: _AdminMediaLocation) -> str:
    metadata = await update_admin_media_search_aliases(location.category_id, location.media_id, ())
    if metadata is None:
        await _show_media_list(message, location)
        return "Медиа больше недоступно."

    await _edit_media_content(
        message,
        location,
        _media_details(metadata),
        (await prepare_admin_media_preview(metadata),),
        _media_keyboard(metadata, location),
    )

    return "Алиасы медиа очищены."


async def _edit_media_content(
    message: Message,
    location: _AdminMediaLocation,
    text: str,
    previews: Sequence[AdminMediaPreview],
    keyboard: InlineKeyboardMarkup,
    *,
    numbered: bool = False,
) -> str | None:
    bot = message.bot
    if bot is None:
        raise RuntimeError("Admin media message has no bot")

    async def edit(*, hide_media: bool = False) -> None:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=location.panel_message_id,
                rich_message=create_admin_media_content(text, previews, numbered=numbered, hide_media=hide_media),
                reply_markup=keyboard,
                parse_mode=None,
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise

    try:
        await edit()
    except TelegramBadRequest as error:
        # Telegram rejects the entire rich update if any attachment is invalid.
        # Replace content and buttons together with numbered placeholders.
        media_errors = ("wrong file", "file identifier", "failed to get http", "failed to get url", "media_empty")
        if not any(reason in error.message.lower() for reason in media_errors):
            raise

        logger.warning("Telegram rejected admin media previews: category_id=%d", location.category_id)
        await edit(hide_media=True)

        return "Предпросмотры недоступны. Можно открыть медиа и редактировать алиасы."

    return None


async def _state_location(state: FSMContext) -> _AdminMediaLocation | None:
    data = await state.get_data()
    values = (
        data.get("category_id"),
        data.get("media_id"),
        data.get("category_page", 0),
        data.get("media_page", 0),
        data.get("panel_message_id"),
    )
    if any(not isinstance(value, int) for value in values):
        await state.clear()
        return None

    category_id, media_id, category_page, media_page, panel_message_id = values
    assert isinstance(category_id, int)
    assert isinstance(media_id, int)
    assert isinstance(category_page, int)
    assert isinstance(media_page, int)
    assert isinstance(panel_message_id, int)

    return _AdminMediaLocation(category_id, media_id, category_page, media_page, panel_message_id)


def _location_from_callback(callback_data: AdminMediaCallbackData, *, message_id: int) -> _AdminMediaLocation:
    return _AdminMediaLocation(
        category_id=callback_data.category_id,
        media_id=callback_data.media_id,
        category_page=callback_data.category_page,
        media_page=callback_data.media_page,
        panel_message_id=message_id,
    )


def _media_keyboard(
    metadata: CategoryMediaSearchMetadata,
    location: _AdminMediaLocation,
) -> InlineKeyboardMarkup:
    return create_admin_media_keyboard(
        category_id=location.category_id,
        media_id=location.media_id,
        has_aliases=bool(metadata.search_aliases),
        category_page=location.category_page,
        media_page=location.media_page,
    )


def _media_details(metadata: CategoryMediaSearchMetadata) -> str:
    media = metadata.media
    aliases = "\n".join(f"• {alias}" for alias in metadata.search_aliases) if metadata.search_aliases else "не заданы"
    activity = "активно" if media.is_active else "неактивно"

    return (
        f"Медиа {media.name}\n\n"
        f"Путь: {media.source_path}\n"
        f"Тип: {media.telegram_media_type.value}\n"
        f"Статус: {media.status.value}, {activity}\n\n"
        f"Алиасы для поиска:\n{aliases}"
    )


def _aliases_prompt(media_name: str) -> str:
    return (
        f"Алиасы медиа {media_name}\n\n"
        "Отправьте алиасы по одному на строку. Новый список полностью заменит текущий.\n"
        "Чтобы удалить все алиасы, вернитесь в карточку и нажмите «Очистить алиасы медиа»."
    )


def _aliases_error(reason: str) -> str:
    return f"{reason}\n\nПопробуйте ещё раз."


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None

    return callback.message

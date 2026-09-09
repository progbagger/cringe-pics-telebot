import logging
from dataclasses import dataclass
from html import escape

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardMarkup, Message

from cringe_pics_telebot.repositories.postgres import CategoryMediaSearchMetadata
from cringe_pics_telebot.services.admin_media import (
    get_admin_media,
    get_admin_media_catalog,
    get_admin_media_preview,
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
from .media import send_image_to_chat

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

    cancel_keyboard = create_admin_media_form_cancel_keyboard(
        category_id=location.category_id,
        media_id=location.media_id,
        category_page=location.category_page,
        media_page=location.media_page,
    )
    if message.text is None:
        await message.answer(
            _aliases_error("Алиасы должны быть текстом."),
            reply_markup=cancel_keyboard,
        )
        return

    try:
        aliases = parse_media_search_aliases(message.text)
    except InvalidMediaSearchAliasesError:
        await message.answer(
            _aliases_error("Не найдено ни одного непустого алиаса."),
            reply_markup=cancel_keyboard,
        )
        return

    metadata = await update_admin_media_search_aliases(
        location.category_id,
        location.media_id,
        aliases,
    )
    await state.clear()
    if metadata is None:
        await message.answer(
            "Медиа больше недоступно.",
            reply_markup=await _media_list_keyboard_or_panel(location),
        )
        return

    await message.answer(
        f"Алиасы медиа обновлены.\n\n{_media_details(metadata)}",
        reply_markup=_media_keyboard(metadata, location),
    )


async def _dispatch_admin_media_callback(
    callback_data: AdminMediaCallbackData,
    *,
    message: Message,
    state: FSMContext,
) -> str | None:
    location = _location_from_callback(callback_data)
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
    catalog = await get_admin_media_catalog(location.category_id)
    if catalog is None:
        await message.edit_text(
            "<b>Админ-панель</b>\n\nКатегория больше недоступна.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return "Категория больше недоступна."

    media_count = len(catalog.media)
    await message.edit_text(
        f"<b>Медиа категории {escape(catalog.category.name)}</b>\n\n"
        f"Активных элементов: <b>{media_count}</b>.\n"
        "Выберите медиа, чтобы просмотреть или изменить его алиасы.",
        reply_markup=create_admin_media_list_keyboard(
            catalog.media,
            category_id=location.category_id,
            category_page=location.category_page,
            media_page=location.media_page,
        ),
    )
    return None


async def _show_media(message: Message, location: _AdminMediaLocation) -> str | None:
    metadata = await get_admin_media(location.category_id, location.media_id)
    if metadata is None:
        await _show_media_list(message, location)
        return "Медиа больше недоступно."

    await message.edit_text(
        _media_details(metadata),
        reply_markup=_media_keyboard(metadata, location),
    )
    return None


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

    await state.set_state(AdminMediaAliasesForm.aliases)
    await state.set_data(
        {
            "category_id": location.category_id,
            "media_id": location.media_id,
            "category_page": location.category_page,
            "media_page": location.media_page,
        }
    )
    await message.edit_text(
        _aliases_prompt(metadata.media.name),
        reply_markup=create_admin_media_form_cancel_keyboard(
            category_id=location.category_id,
            media_id=location.media_id,
            category_page=location.category_page,
            media_page=location.media_page,
        ),
    )
    return await _send_media_preview(message, metadata)


async def _clear_aliases(message: Message, location: _AdminMediaLocation) -> str:
    metadata = await update_admin_media_search_aliases(location.category_id, location.media_id, ())
    if metadata is None:
        await _show_media_list(message, location)
        return "Медиа больше недоступно."

    await message.edit_text(
        _media_details(metadata),
        reply_markup=_media_keyboard(metadata, location),
    )
    return "Алиасы медиа очищены."


async def _send_media_preview(
    message: Message,
    metadata: CategoryMediaSearchMetadata,
) -> str | None:
    try:
        preview = await get_admin_media_preview(metadata)
        if preview is None:
            return "Не удалось получить предпросмотр медиа."
        bot = message.bot
        if bot is None:
            return "Не удалось отправить предпросмотр медиа."
        await send_image_to_chat(bot=bot, chat_id=message.chat.id, image=preview)
    except Exception:
        logger.exception("Failed to send admin preview for media %d", metadata.media.id)
        return "Не удалось отправить предпросмотр медиа."
    return None


async def _media_list_keyboard_or_panel(location: _AdminMediaLocation) -> InlineKeyboardMarkup:
    catalog = await get_admin_media_catalog(location.category_id)
    if catalog is None:
        return create_admin_panel_keyboard()
    return create_admin_media_list_keyboard(
        catalog.media,
        category_id=location.category_id,
        category_page=location.category_page,
        media_page=location.media_page,
    )


async def _state_location(state: FSMContext) -> _AdminMediaLocation | None:
    data = await state.get_data()
    values = (
        data.get("category_id"),
        data.get("media_id"),
        data.get("category_page", 0),
        data.get("media_page", 0),
    )
    if any(not isinstance(value, int) for value in values):
        await state.clear()
        return None
    category_id, media_id, category_page, media_page = values
    assert isinstance(category_id, int)
    assert isinstance(media_id, int)
    assert isinstance(category_page, int)
    assert isinstance(media_page, int)
    return _AdminMediaLocation(category_id, media_id, category_page, media_page)


def _location_from_callback(callback_data: AdminMediaCallbackData) -> _AdminMediaLocation:
    return _AdminMediaLocation(
        category_id=callback_data.category_id,
        media_id=callback_data.media_id,
        category_page=callback_data.category_page,
        media_page=callback_data.media_page,
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
    aliases = (
        "\n".join(f"• <code>{escape(alias)}</code>" for alias in metadata.search_aliases)
        if metadata.search_aliases
        else "<i>не заданы</i>"
    )
    activity = "активно" if media.is_active else "неактивно"
    return (
        f"<b>Медиа {escape(media.name)}</b>\n\n"
        f"Путь: <code>{escape(media.source_path)}</code>\n"
        f"Тип: <code>{escape(media.telegram_media_type.value)}</code>\n"
        f"Статус: <code>{escape(media.status.value)}</code>, {activity}\n\n"
        f"Алиасы для поиска:\n{aliases}"
    )


def _aliases_prompt(media_name: str) -> str:
    return (
        f"<b>Алиасы медиа {escape(media_name)}</b>\n\n"
        "Отправьте алиасы по одному на строку. Новый список полностью заменит текущий.\n"
        "Чтобы удалить все алиасы, вернитесь в карточку и нажмите «Очистить алиасы медиа»."
    )


def _aliases_error(reason: str) -> str:
    return f"{reason}\n\nПопробуйте ещё раз."


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None
    return callback.message

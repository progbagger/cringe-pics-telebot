import logging
from html import escape

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from cringe_pics_telebot.repositories.postgres import (
    get_subscription_type,
    get_subscription_types,
    transaction,
    update_subscription_type_search_aliases,
)
from cringe_pics_telebot.repositories.postgres.entities import SubscriptionType
from cringe_pics_telebot.services.category_aliases import (
    InvalidCategoryAliasesError,
    parse_category_search_aliases,
)

from .admin_access import IsAdministrator
from .admin_category_callback_data import AdminCategoryAction, AdminCategoryCallbackData
from .admin_keyboards import (
    create_admin_categories_keyboard,
    create_admin_category_form_cancel_keyboard,
    create_admin_category_keyboard,
    create_admin_panel_keyboard,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_categories")
router.message.filter(IsAdministrator())
router.callback_query.filter(IsAdministrator())


class AdminCategoryAliasesForm(StatesGroup):
    aliases = State()


@router.callback_query(AdminCategoryCallbackData.filter())
async def handle_admin_category_callback(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    if message is None or callback.data is None:
        await callback.answer("Сообщение панели недоступно.", show_alert=True)
        return

    callback_data = AdminCategoryCallbackData.unpack(callback.data)
    try:
        notice = await _dispatch_admin_category_callback(
            callback_data=callback_data,
            message=message,
            state=state,
        )
    except Exception:
        logger.exception("Failed to process admin category callback %s", callback.data)
        await callback.answer("Не удалось выполнить действие.", show_alert=True)
        return

    await callback.answer(notice, show_alert=notice is not None)


@router.message(AdminCategoryAliasesForm.aliases)
async def receive_category_aliases(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(_aliases_error("Алиасы должны быть текстом."))
        return

    try:
        search_aliases = parse_category_search_aliases(message.text)
    except InvalidCategoryAliasesError:
        await message.answer(_aliases_error("Не найдено ни одного непустого алиаса."))
        return

    category_id = await _state_category_id(state)
    if category_id is None:
        await message.answer(
            "Черновик потерян. Откройте категорию заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return

    async with transaction():
        updated = await update_subscription_type_search_aliases(category_id, search_aliases)
    await state.clear()

    category = await get_subscription_type(category_id) if updated else None
    if category is None:
        await message.answer(
            "Категория больше недоступна.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return

    await message.answer(
        f"Алиасы категории обновлены.\n\n{_category_details(category)}",
        reply_markup=create_admin_category_keyboard(category.id, has_aliases=True),
    )


async def _dispatch_admin_category_callback(
    *,
    callback_data: AdminCategoryCallbackData,
    message: Message,
    state: FSMContext,
) -> str | None:
    match callback_data.action:
        case AdminCategoryAction.categories:
            await state.clear()
            await _show_categories(message)
        case AdminCategoryAction.category:
            await state.clear()
            return await _show_category(message, callback_data.category_id)
        case AdminCategoryAction.edit_aliases:
            return await _start_edit_aliases(message, state, callback_data.category_id)
        case AdminCategoryAction.clear_aliases:
            await state.clear()
            return await _clear_aliases(message, callback_data.category_id)
        case AdminCategoryAction.cancel_form:
            await state.clear()
            await message.edit_text(
                "<b>Админ-панель</b>\n\nРедактирование алиасов отменено.",
                reply_markup=create_admin_panel_keyboard(),
            )
    return None


async def _show_categories(message: Message) -> None:
    subscription_types = await get_subscription_types()
    await message.edit_text(
        "<b>Управление категориями</b>\n\nВыберите категорию.",
        reply_markup=create_admin_categories_keyboard(subscription_types),
    )


async def _show_category(message: Message, category_id: int) -> str | None:
    category = await get_subscription_type(category_id)
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."

    await message.edit_text(
        _category_details(category),
        reply_markup=create_admin_category_keyboard(category.id, has_aliases=bool(category.search_aliases)),
    )
    return None


async def _start_edit_aliases(message: Message, state: FSMContext, category_id: int) -> str | None:
    category = await get_subscription_type(category_id)
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."

    await state.set_state(AdminCategoryAliasesForm.aliases)
    await state.set_data({"category_id": category_id})
    await message.edit_text(
        _aliases_prompt(category.name),
        reply_markup=create_admin_category_form_cancel_keyboard(),
    )
    return None


async def _clear_aliases(message: Message, category_id: int) -> str:
    async with transaction():
        updated = await update_subscription_type_search_aliases(category_id, ())
    if not updated:
        await _show_categories(message)
        return "Категория больше недоступна."

    await _show_category(message, category_id)
    return "Алиасы очищены."


async def _state_category_id(state: FSMContext) -> int | None:
    category_id = (await state.get_data()).get("category_id")
    if not isinstance(category_id, int):
        await state.clear()
        return None
    return category_id


def _category_details(category: SubscriptionType) -> str:
    if category.search_aliases:
        aliases = "\n".join(f"• <code>{escape(alias)}</code>" for alias in category.search_aliases)
    else:
        aliases = "<i>не заданы</i>"
    return f"<b>Категория {escape(category.name)}</b>\n\nАлиасы для inline-поиска:\n{aliases}"


def _aliases_prompt(category_name: str) -> str:
    return (
        f"<b>Алиасы категории {escape(category_name)}</b>\n\n"
        "Отправьте новый полный список: один алиас на строку. "
        "Пробелы внутри алиаса сохраняются, пустые строки и повторы игнорируются. "
        "Чтобы удалить все алиасы, вернитесь в карточку и нажмите «Очистить алиасы»."
    )


def _aliases_error(reason: str) -> str:
    return f"{reason}\n\nОтправьте по одному алиасу на строку или нажмите «Отмена»."


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None
    return callback.message

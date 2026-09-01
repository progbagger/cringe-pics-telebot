import logging
from datetime import time
from html import escape

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardMarkup, Message

from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.repositories.postgres import (
    CreateSubscriptionType,
    get_all_subscription_types,
    get_subscription_type,
    transaction,
    update_subscription_type_search_aliases,
)
from cringe_pics_telebot.repositories.postgres.entities import SubscriptionType
from cringe_pics_telebot.services.admin_categories import (
    AdminCategoryNameConflictError,
    InvalidAdminCategoryNameError,
    InvalidAdminCategoryPathError,
    InvalidAdminCategoryTimeError,
    admin_category_name_exists,
    create_admin_category,
    parse_admin_category_name,
    parse_admin_category_path,
    parse_admin_category_time,
    set_admin_category_activity,
    set_admin_category_time,
    set_admin_category_weekdays,
)
from cringe_pics_telebot.services.category_aliases import (
    InvalidCategoryAliasesError,
    parse_category_search_aliases,
)
from cringe_pics_telebot.services.subscription_schedules import format_subscription_weekdays

from .admin_access import IsAdministrator
from .admin_category_callback_data import AdminCategoryAction, AdminCategoryCallbackData
from .admin_keyboards import (
    create_admin_categories_keyboard,
    create_admin_category_form_cancel_keyboard,
    create_admin_category_keyboard,
    create_admin_category_schedule_mode_keyboard,
    create_admin_category_weekdays_keyboard,
    create_admin_panel_keyboard,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_categories")
router.message.filter(IsAdministrator())
router.callback_query.filter(IsAdministrator())


class AdminCategoryCreationForm(StatesGroup):
    name = State()
    s3_directory_path = State()
    schedule_mode = State()
    send_time = State()
    weekdays = State()
    aliases = State()


class AdminCategoryTimeForm(StatesGroup):
    send_time = State()


class AdminCategoryWeekdaysForm(StatesGroup):
    weekdays = State()


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


@router.message(AdminCategoryCreationForm.name)
async def receive_new_category_name(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(
            _name_error("Название категории должно быть текстом."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    try:
        name = parse_admin_category_name(message.text)
    except InvalidAdminCategoryNameError:
        await message.answer(
            _name_error("Название категории не должно быть пустым."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    if await admin_category_name_exists(name):
        await message.answer(
            _name_error("Категория с таким названием уже существует."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    await state.update_data(name=name)
    await state.set_state(AdminCategoryCreationForm.s3_directory_path)
    await message.answer(
        _path_prompt(),
        reply_markup=create_admin_category_form_cancel_keyboard(),
    )


@router.message(AdminCategoryCreationForm.s3_directory_path)
async def receive_new_category_path(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(
            _path_error("Путь к каталогу должен быть текстом."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    try:
        path = parse_admin_category_path(message.text)
    except InvalidAdminCategoryPathError:
        await message.answer(
            _path_error("Путь к каталогу не должен быть пустым."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    await state.update_data(s3_directory_path=path)
    await state.set_state(AdminCategoryCreationForm.schedule_mode)
    await message.answer(
        _schedule_mode_prompt(),
        reply_markup=create_admin_category_schedule_mode_keyboard(),
    )


@router.message(AdminCategoryCreationForm.send_time)
async def receive_new_category_time(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(
            _time_error("Время отправки должно быть текстом."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    try:
        send_time = parse_admin_category_time(message.text)
    except InvalidAdminCategoryTimeError:
        await message.answer(
            _time_error("Не удалось распознать локальное время отправки."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    await state.update_data(send_time=send_time, weekdays=())
    await state.set_state(AdminCategoryCreationForm.weekdays)
    await message.answer(
        _new_category_weekdays_prompt(),
        reply_markup=create_admin_category_weekdays_keyboard(()),
    )


@router.message(AdminCategoryTimeForm.send_time)
async def receive_category_time(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(
            _edit_time_error("Время отправки должно быть текстом."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    try:
        send_time = parse_admin_category_time(message.text)
    except InvalidAdminCategoryTimeError:
        await message.answer(
            _edit_time_error("Не удалось распознать локальное время отправки."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    category_id = await _state_category_id(state)
    if category_id is None:
        await message.answer(
            "Черновик потерян. Откройте категорию заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return

    category = await set_admin_category_time(category_id, send_time)
    await state.clear()
    if category is None:
        await message.answer(
            "Категория больше недоступна.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return

    await message.answer(
        f"Время отправки обновлено.\n\n{_category_details(category)}",
        reply_markup=_category_keyboard(category),
    )


@router.message(AdminCategoryCreationForm.aliases)
async def receive_new_category_aliases(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(
            _aliases_error("Алиасы должны быть текстом."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    try:
        search_aliases = parse_category_search_aliases(message.text)
    except InvalidCategoryAliasesError:
        await message.answer(
            _aliases_error("Не найдено ни одного непустого алиаса."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    draft = await _state_creation_draft(state, search_aliases=search_aliases)
    if draft is None:
        await message.answer(
            "Черновик потерян. Начните создание категории заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return

    try:
        category = await create_admin_category(draft)
    except AdminCategoryNameConflictError:
        await state.clear()
        await state.set_state(AdminCategoryCreationForm.name)
        await message.answer(
            _name_error("Категория с таким названием уже была создана. Введите другое название."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        f"Категория создана неактивной.\n\n{_category_details(category)}",
        reply_markup=_category_keyboard(category),
    )


@router.message(AdminCategoryAliasesForm.aliases)
async def receive_category_aliases(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(
            _aliases_error("Алиасы должны быть текстом."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return

    try:
        search_aliases = parse_category_search_aliases(message.text)
    except InvalidCategoryAliasesError:
        await message.answer(
            _aliases_error("Не найдено ни одного непустого алиаса."),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
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
        reply_markup=_category_keyboard(category),
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
        case AdminCategoryAction.create:
            await _start_new_category(message, state)
        case AdminCategoryAction.create_scheduled:
            return await _select_new_category_schedule(message, state, scheduled=True)
        case AdminCategoryAction.create_without_schedule:
            return await _select_new_category_schedule(message, state, scheduled=False)
        case AdminCategoryAction.activate:
            await state.clear()
            return await _set_category_activity(message, callback_data.category_id, is_active=True)
        case AdminCategoryAction.deactivate:
            await state.clear()
            return await _set_category_activity(message, callback_data.category_id, is_active=False)
        case AdminCategoryAction.edit_time:
            return await _start_edit_time(message, state, callback_data.category_id)
        case AdminCategoryAction.disable_schedule:
            await state.clear()
            return await _disable_schedule(message, callback_data.category_id)
        case AdminCategoryAction.edit_weekdays:
            return await _start_edit_weekdays(message, state, callback_data.category_id)
        case AdminCategoryAction.toggle_weekday:
            return await _toggle_weekday(message, state, callback_data.weekday)
        case AdminCategoryAction.confirm_weekdays:
            return await _confirm_weekdays(message, state)
        case AdminCategoryAction.daily_weekdays:
            return await _confirm_weekdays(message, state, weekdays=SubscriptionWeekdays.daily())
        case AdminCategoryAction.edit_aliases:
            return await _start_edit_aliases(message, state, callback_data.category_id)
        case AdminCategoryAction.clear_aliases:
            await state.clear()
            return await _clear_aliases(message, callback_data.category_id)
        case AdminCategoryAction.cancel_form:
            await state.clear()
            await message.edit_text(
                "<b>Админ-панель</b>\n\nСоздание или редактирование категории отменено.",
                reply_markup=create_admin_panel_keyboard(),
            )
    return None


async def _show_categories(message: Message) -> None:
    subscription_types = await get_all_subscription_types()
    await message.edit_text(
        "<b>Управление категориями</b>\n\nВыберите категорию или создайте новую.",
        reply_markup=create_admin_categories_keyboard(subscription_types),
    )


async def _show_category(message: Message, category_id: int) -> str | None:
    category = await get_subscription_type(category_id)
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."

    await message.edit_text(
        _category_details(category),
        reply_markup=_category_keyboard(category),
    )
    return None


async def _start_new_category(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminCategoryCreationForm.name)
    await message.edit_text(
        _name_prompt(),
        reply_markup=create_admin_category_form_cancel_keyboard(),
    )


async def _select_new_category_schedule(message: Message, state: FSMContext, *, scheduled: bool) -> str | None:
    data = await state.get_data()
    if not isinstance(data.get("name"), str) or not isinstance(data.get("s3_directory_path"), str):
        await state.clear()
        await message.edit_text(
            "<b>Админ-панель</b>\n\nЧерновик потерян. Начните создание категории заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return "Черновик создания категории потерян."

    if scheduled:
        await state.set_state(AdminCategoryCreationForm.send_time)
        await message.edit_text(
            _time_prompt(),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return None

    await state.update_data(send_time=None, weekdays=SubscriptionWeekdays.daily().days)
    await state.set_state(AdminCategoryCreationForm.aliases)
    await message.edit_text(
        _new_category_aliases_prompt(),
        reply_markup=create_admin_category_form_cancel_keyboard(),
    )
    return None


async def _set_category_activity(message: Message, category_id: int, *, is_active: bool) -> str:
    category = await set_admin_category_activity(category_id, is_active=is_active)
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."

    await message.edit_text(
        _category_details(category),
        reply_markup=_category_keyboard(category),
    )
    return "Категория активирована." if is_active else "Категория деактивирована."


async def _start_edit_time(message: Message, state: FSMContext, category_id: int) -> str | None:
    category = await get_subscription_type(category_id)
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."

    await state.set_state(AdminCategoryTimeForm.send_time)
    await state.set_data({"category_id": category_id})
    await message.edit_text(
        _edit_time_prompt(),
        reply_markup=create_admin_category_form_cancel_keyboard(),
    )
    return None


async def _disable_schedule(message: Message, category_id: int) -> str:
    category = await set_admin_category_time(category_id, None)
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."

    await message.edit_text(
        _category_details(category),
        reply_markup=_category_keyboard(category),
    )
    return "Расписание отключено."


async def _start_edit_weekdays(message: Message, state: FSMContext, category_id: int) -> str | None:
    category = await get_subscription_type(category_id)
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."
    if category.time is None:
        await _show_category(message, category_id)
        return "Расписание категории отключено."

    await state.set_state(AdminCategoryWeekdaysForm.weekdays)
    await state.set_data({"category_id": category_id, "weekdays": category.weekdays.days})
    await message.edit_text(
        _edit_category_weekdays_prompt(category.name),
        reply_markup=create_admin_category_weekdays_keyboard(category.weekdays),
    )
    return None


async def _toggle_weekday(message: Message, state: FSMContext, weekday: int) -> str | None:
    if weekday not in range(1, 8):
        return "Некорректный день недели."

    weekdays = await _state_weekdays(state, allow_empty=True)
    if weekdays is None:
        await message.edit_text(
            "<b>Админ-панель</b>\n\nЧерновик потерян. Начните действие заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return "Черновик выбора дней потерян."

    selected = set(weekdays)
    if weekday in selected:
        selected.remove(weekday)
    else:
        selected.add(weekday)
    normalized = tuple(sorted(selected))
    await state.update_data(weekdays=normalized)
    await message.edit_reply_markup(reply_markup=create_admin_category_weekdays_keyboard(normalized))
    return None


async def _confirm_weekdays(
    message: Message,
    state: FSMContext,
    *,
    weekdays: SubscriptionWeekdays | None = None,
) -> str | None:
    selected = weekdays or await _state_weekdays(state, allow_empty=True)
    if selected is None:
        await message.edit_text(
            "<b>Админ-панель</b>\n\nЧерновик потерян. Начните действие заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return "Черновик выбора дней потерян."
    if not selected:
        return "Выберите хотя бы один день."

    normalized = selected if isinstance(selected, SubscriptionWeekdays) else SubscriptionWeekdays(*selected)
    current_state = await state.get_state()
    if current_state == AdminCategoryCreationForm.weekdays.state:
        await state.update_data(weekdays=normalized.days)
        await state.set_state(AdminCategoryCreationForm.aliases)
        await message.edit_text(
            _new_category_aliases_prompt(),
            reply_markup=create_admin_category_form_cancel_keyboard(),
        )
        return None
    if current_state != AdminCategoryWeekdaysForm.weekdays.state:
        await state.clear()
        await message.edit_text(
            "<b>Админ-панель</b>\n\nЧерновик потерян. Начните действие заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return "Черновик выбора дней потерян."

    category_id = await _state_category_id(state)
    if category_id is None:
        await message.edit_text(
            "<b>Админ-панель</b>\n\nЧерновик потерян. Откройте категорию заново.",
            reply_markup=create_admin_panel_keyboard(),
        )
        return "Черновик выбора дней потерян."

    category = await set_admin_category_weekdays(category_id, normalized)
    await state.clear()
    if category is None:
        await _show_categories(message)
        return "Категория больше недоступна."

    await message.edit_text(
        _category_details(category),
        reply_markup=_category_keyboard(category),
    )
    return "Дни отправки обновлены."


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


async def _state_creation_draft(
    state: FSMContext,
    *,
    search_aliases: tuple[str, ...],
) -> CreateSubscriptionType | None:
    data = await state.get_data()
    name = data.get("name")
    s3_directory_path = data.get("s3_directory_path")
    if "send_time" not in data:
        await state.clear()
        return None
    send_time = data["send_time"]
    weekdays = _weekdays_from_state_data(data)
    if (
        not isinstance(name, str)
        or not isinstance(s3_directory_path, str)
        or (send_time is not None and not isinstance(send_time, time))
        or weekdays is None
    ):
        await state.clear()
        return None

    return CreateSubscriptionType(
        name=name,
        time=send_time,
        s3_directory_path=s3_directory_path,
        search_aliases=search_aliases,
        weekdays=weekdays,
    )


async def _state_category_id(state: FSMContext) -> int | None:
    category_id = (await state.get_data()).get("category_id")
    if not isinstance(category_id, int):
        await state.clear()
        return None
    return category_id


async def _state_weekdays(
    state: FSMContext,
    *,
    allow_empty: bool,
) -> SubscriptionWeekdays | tuple[int, ...] | None:
    data = await state.get_data()
    raw_weekdays = data.get("weekdays")
    if not isinstance(raw_weekdays, (list, tuple)) or any(not isinstance(day, int) for day in raw_weekdays):
        await state.clear()
        return None
    if not raw_weekdays:
        return () if allow_empty else None
    try:
        return SubscriptionWeekdays(*raw_weekdays)
    except ValueError:
        await state.clear()
        return None


def _weekdays_from_state_data(data: dict[str, object]) -> SubscriptionWeekdays | None:
    raw_weekdays = data.get("weekdays")
    if not isinstance(raw_weekdays, (list, tuple)) or not raw_weekdays:
        return None
    if any(not isinstance(day, int) for day in raw_weekdays):
        return None
    try:
        return SubscriptionWeekdays(*raw_weekdays)
    except ValueError:
        return None


def _category_keyboard(category: SubscriptionType) -> InlineKeyboardMarkup:
    return create_admin_category_keyboard(
        category.id,
        has_aliases=bool(category.search_aliases),
        has_schedule=category.time is not None,
        is_active=category.is_active,
    )


def _category_details(category: SubscriptionType) -> str:
    if category.search_aliases:
        aliases = "\n".join(f"• <code>{escape(alias)}</code>" for alias in category.search_aliases)
    else:
        aliases = "<i>не заданы</i>"
    status = "активна" if category.is_active else "неактивна"
    send_time = f"<code>{category.time.strftime('%H:%M')}</code>" if category.time is not None else "без расписания"
    weekdays = format_subscription_weekdays(category.weekdays, daily_label="каждый день")
    return (
        f"<b>Категория {escape(category.name)}</b>\n\n"
        f"Статус: <b>{status}</b>\n"
        f"Путь к каталогу: <code>{escape(category.s3_directory_path)}</code>\n"
        f"Локальное время отправки: {send_time}\n"
        f"Дни отправки: {weekdays}\n\n"
        f"Алиасы для inline-поиска:\n{aliases}"
    )


def _name_prompt(*, prefix: str = "Отправьте название новой категории.") -> str:
    return f"<b>Новая категория — название</b>\n\n{prefix}"


def _name_error(reason: str) -> str:
    return f"{reason}\n\n{_name_prompt(prefix='Попробуйте ещё раз.')}"


def _path_prompt(*, prefix: str = "Отправьте путь к каталогу с медиа на Яндекс Диске.") -> str:
    return f"<b>Новая категория — путь к каталогу</b>\n\n{prefix}\nНапример: <code>afternoon</code>."


def _path_error(reason: str) -> str:
    return f"{reason}\n\n{_path_prompt(prefix='Попробуйте ещё раз.')}"


def _schedule_mode_prompt() -> str:
    return (
        "<b>Новая категория — режим отправки</b>\n\n"
        "Выберите «По расписанию», чтобы задать локальное время и дни рассылки, "
        "или «Без расписания» для выдачи только по пользовательской кнопке."
    )


def _time_prompt(*, prefix: str = "Введите локальное время отправки категории.") -> str:
    return (
        f"<b>Новая категория — время отправки</b>\n\n{prefix}\nФормат: <code>ЧЧ:ММ</code>, например <code>15:30</code>."
    )


def _time_error(reason: str) -> str:
    return f"{reason}\n\n{_time_prompt(prefix='Попробуйте ещё раз.')}"


def _new_category_weekdays_prompt() -> str:
    return (
        "<b>Новая категория — дни отправки</b>\n\n"
        "Выберите хотя бы один день недели и нажмите «Готово» либо используйте «Каждый день»."
    )


def _edit_category_weekdays_prompt(category_name: str) -> str:
    return (
        f"<b>Дни отправки категории {escape(category_name)}</b>\n\n"
        "Измените выбранные дни и нажмите «Готово» либо используйте «Каждый день»."
    )


def _edit_time_prompt(*, prefix: str = "Введите новое локальное время отправки категории.") -> str:
    return f"<b>Изменение времени отправки</b>\n\n{prefix}\nФормат: <code>ЧЧ:ММ</code>, например <code>15:30</code>."


def _edit_time_error(reason: str) -> str:
    return f"{reason}\n\n{_edit_time_prompt(prefix='Попробуйте ещё раз.')}"


def _new_category_aliases_prompt() -> str:
    return (
        "<b>Новая категория — алиасы</b>\n\n"
        "Отправьте алиасы для inline-поиска: один алиас на строку. "
        "Пробелы внутри алиаса сохраняются, пустые строки и повторы игнорируются."
    )


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

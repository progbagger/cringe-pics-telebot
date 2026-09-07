import logging
from datetime import UTC, datetime, timedelta, timezone

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from cringe_pics_telebot.repositories.postgres import (
    create_admin_broadcast,
    get_admin_broadcast,
    get_admin_broadcast_recipient_ids,
    get_scheduled_admin_broadcasts,
    set_admin_broadcast_recipients,
    soft_delete_admin_broadcast,
    transaction,
    update_admin_broadcast_message,
    update_admin_broadcast_schedule,
)
from cringe_pics_telebot.repositories.postgres.entities import AdminBroadcast, AdminBroadcastStatus
from cringe_pics_telebot.services.admin_broadcast_recipients import (
    MAX_EXTRA_RECIPIENTS,
    InvalidAdminBroadcastRecipientsError,
    parse_admin_broadcast_recipient_ids,
)
from cringe_pics_telebot.services.admin_broadcast_schedules import (
    AdminBroadcastSchedule,
    InvalidAdminBroadcastScheduleError,
    PastAdminBroadcastScheduleError,
    format_admin_broadcast_countdown,
    format_admin_broadcast_schedule,
    parse_admin_broadcast_schedule,
)
from cringe_pics_telebot.services.timezones import (
    DEFAULT_TIMEZONE_OFFSET_MINUTES,
    format_timezone_offset,
    get_user_timezone_offset,
)

from .admin_access import IsAdministrator
from .admin_broadcast_callback_data import (
    AdminBroadcastAction,
    AdminBroadcastCallbackData,
    AdminBroadcastPagedCallbackData,
)
from .admin_keyboards import (
    create_admin_broadcast_delete_keyboard,
    create_admin_broadcast_keyboard,
    create_admin_broadcasts_keyboard,
    create_admin_form_cancel_keyboard,
    create_admin_panel_keyboard,
    create_admin_recipients_keyboard,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_broadcasts")
router.message.filter(IsAdministrator())
router.callback_query.filter(IsAdministrator())


class AdminBroadcastForm(StatesGroup):
    new_message = State()
    new_schedule = State()
    new_recipients = State()
    edited_message = State()
    edited_schedule = State()
    edited_recipients = State()


@router.callback_query(AdminBroadcastPagedCallbackData.filter())
@router.callback_query(AdminBroadcastCallbackData.filter())
async def handle_admin_broadcast_callback(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    if message is None or callback.data is None:
        await callback.answer("Сообщение панели недоступно.", show_alert=True)
        return

    callback_data = _unpack_broadcast_callback(callback.data)
    try:
        notice = await _dispatch_admin_broadcast_callback(
            callback_data=callback_data,
            message=message,
            state=state,
            viewer_user_id=callback.from_user.id,
        )
    except Exception:
        logger.exception("Failed to process admin callback %s", callback.data)
        await callback.answer("Не удалось выполнить действие.", show_alert=True)
        return

    await callback.answer(notice, show_alert=notice is not None)


@router.message(AdminBroadcastForm.new_message)
async def receive_new_broadcast_message(message: Message, state: FSMContext) -> None:
    assert message.from_user is not None
    await state.update_data(
        created_by_user_id=message.from_user.id,
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await state.set_state(AdminBroadcastForm.new_schedule)
    await message.answer(
        _schedule_prompt(),
        reply_markup=create_admin_form_cancel_keyboard(),
    )


@router.message(AdminBroadcastForm.new_schedule)
async def receive_new_broadcast_schedule(message: Message, state: FSMContext) -> None:
    schedule = await _parse_schedule_message(message)
    if schedule is None:
        return

    await state.update_data(
        scheduled_local_at=schedule.local_at,
        timezone_offset_minutes=schedule.timezone_offset_minutes,
    )
    await state.set_state(AdminBroadcastForm.new_recipients)
    await message.answer(
        _recipients_prompt(),
        reply_markup=create_admin_recipients_keyboard(),
    )


@router.message(AdminBroadcastForm.new_recipients)
async def receive_new_broadcast_recipients(message: Message, state: FSMContext) -> None:
    recipient_ids = await _parse_recipient_ids_message(message)
    if recipient_ids is None:
        return
    page = await _state_page(state)
    broadcast = await _create_broadcast_from_state(message=message, state=state, recipient_ids=recipient_ids)
    if broadcast is None:
        return
    await _answer_with_broadcast_list(
        message,
        prefix=_created_broadcast_text(broadcast, len(recipient_ids)),
        page=page,
    )


@router.message(AdminBroadcastForm.edited_message)
async def receive_edited_broadcast_message(message: Message, state: FSMContext) -> None:
    broadcast_id = await _state_broadcast_id(state)
    page = await _state_page(state)
    if broadcast_id is None:
        await message.answer("Черновик потерян. Откройте уведомление заново.")
        return

    async with transaction():
        updated = await update_admin_broadcast_message(
            broadcast_id=broadcast_id,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
    await state.clear()
    if not updated:
        await _answer_with_broadcast_list(
            message,
            prefix="Отправка уведомления уже началась или оно недоступно.",
            page=page,
        )
        return

    await _answer_with_broadcast_list(message, prefix="Сообщение уведомления обновлено.", page=page)


@router.message(AdminBroadcastForm.edited_schedule)
async def receive_edited_broadcast_schedule(message: Message, state: FSMContext) -> None:
    schedule = await _parse_schedule_message(message)
    if schedule is None:
        return

    broadcast_id = await _state_broadcast_id(state)
    page = await _state_page(state)
    if broadcast_id is None:
        await message.answer("Черновик потерян. Откройте уведомление заново.")
        return

    async with transaction():
        updated = await update_admin_broadcast_schedule(
            broadcast_id=broadcast_id,
            scheduled_local_at=schedule.local_at,
            timezone_offset_minutes=schedule.timezone_offset_minutes,
        )
    await state.clear()
    if not updated:
        await _answer_with_broadcast_list(
            message,
            prefix="Отправка уведомления уже началась или оно недоступно.",
            page=page,
        )
        return

    await _answer_with_broadcast_list(message, prefix="Дата и время уведомления обновлены.", page=page)


@router.message(AdminBroadcastForm.edited_recipients)
async def receive_edited_broadcast_recipients(message: Message, state: FSMContext) -> None:
    recipient_ids = await _parse_recipient_ids_message(message)
    if recipient_ids is None:
        return
    broadcast_id = await _state_broadcast_id(state)
    page = await _state_page(state)
    if broadcast_id is None:
        await message.answer("Черновик потерян. Откройте уведомление заново.")
        return
    async with transaction():
        updated = await set_admin_broadcast_recipients(
            broadcast_id=broadcast_id,
            user_ids=recipient_ids,
        )
    await state.clear()
    if not updated:
        await _answer_with_broadcast_list(
            message,
            prefix="Отправка уведомления уже началась или оно недоступно.",
            page=page,
        )
        return
    await _answer_with_broadcast_list(message, prefix="Дополнительные получатели обновлены.", page=page)


async def _dispatch_admin_broadcast_callback(
    *,
    callback_data: AdminBroadcastPagedCallbackData,
    message: Message,
    state: FSMContext,
    viewer_user_id: int,
) -> str | None:
    match callback_data.action:
        case AdminBroadcastAction.broadcasts:
            await state.clear()
            return await _show_broadcasts_or_start(message=message, state=state, page=callback_data.page)
        case AdminBroadcastAction.page:
            await state.clear()
            await _edit_current_broadcast_list(message, page=callback_data.page)
        case AdminBroadcastAction.new_broadcast:
            await _start_new_broadcast(message=message, state=state, page=callback_data.page)
        case AdminBroadcastAction.edit_broadcast:
            await state.clear()
            return await _show_broadcast(
                message,
                callback_data.broadcast_id,
                viewer_user_id=viewer_user_id,
                page=callback_data.page,
            )
        case AdminBroadcastAction.edit_message:
            return await _start_edit_message(message, state, callback_data.broadcast_id, page=callback_data.page)
        case AdminBroadcastAction.edit_schedule:
            return await _start_edit_schedule(message, state, callback_data.broadcast_id, page=callback_data.page)
        case AdminBroadcastAction.edit_recipients:
            return await _start_edit_recipients(message, state, callback_data.broadcast_id, page=callback_data.page)
        case AdminBroadcastAction.delete_broadcast:
            await state.clear()
            return await _show_delete_confirmation(message, callback_data.broadcast_id, page=callback_data.page)
        case AdminBroadcastAction.confirm_delete:
            await state.clear()
            return await _delete_broadcast(message, callback_data.broadcast_id, page=callback_data.page)
        case AdminBroadcastAction.cancel_form:
            page = await _state_page(state)
            await state.clear()
            await _edit_current_broadcast_list(
                message,
                page=page,
                prefix="Создание или редактирование отменено.",
            )
        case AdminBroadcastAction.skip_recipients:
            page = await _state_page(state)
            broadcast = await _create_broadcast_from_state(message=message, state=state, recipient_ids=set())
            if broadcast is None:
                return "Черновик потерян. Начните создание уведомления заново."
            await _edit_current_broadcast_list(message, page=page)
            return _created_broadcast_text(broadcast, 0, html=False)
    return None


async def _show_broadcasts_or_start(*, message: Message, state: FSMContext, page: int = 0) -> str | None:
    broadcasts = await get_scheduled_admin_broadcasts()
    if not broadcasts:
        await _start_new_broadcast(message=message, state=state, page=page)
        return None
    await _edit_with_broadcast_list(message, broadcasts, page=page)
    return None


async def _start_new_broadcast(*, message: Message, state: FSMContext, page: int = 0) -> None:
    await state.clear()
    await state.set_state(AdminBroadcastForm.new_message)
    await state.set_data({"page": page})
    await message.edit_text(
        "<b>Новое уведомление</b>\n\nОтправьте сообщение, которое нужно доставить получателям.",
        reply_markup=create_admin_form_cancel_keyboard(),
    )


async def _show_broadcast(
    message: Message,
    broadcast_id: int,
    *,
    viewer_user_id: int,
    page: int,
) -> str | None:
    broadcast = await _editable_broadcast(broadcast_id)
    if broadcast is None:
        await _edit_current_broadcast_list(message, page=page)
        return "Отправка уведомления уже началась или оно недоступно."
    recipient_ids = await get_admin_broadcast_recipient_ids(broadcast.id)
    viewer_timezone_offset_minutes = await get_user_timezone_offset(viewer_user_id)
    await message.edit_text(
        _broadcast_details(
            broadcast,
            extra_recipient_count=len(recipient_ids),
            viewer_timezone_offset_minutes=viewer_timezone_offset_minutes,
        ),
        reply_markup=create_admin_broadcast_keyboard(broadcast.id, page=page),
    )
    return None


async def _start_edit_message(
    message: Message,
    state: FSMContext,
    broadcast_id: int,
    *,
    page: int,
) -> str | None:
    if await _editable_broadcast(broadcast_id) is None:
        await _edit_current_broadcast_list(message, page=page)
        return "Отправка уведомления уже началась или оно недоступно."
    await state.set_state(AdminBroadcastForm.edited_message)
    await state.set_data({"broadcast_id": broadcast_id, "page": page})
    await message.edit_text(
        "Отправьте новое сообщение для уведомления.",
        reply_markup=create_admin_form_cancel_keyboard(),
    )
    return None


async def _start_edit_schedule(
    message: Message,
    state: FSMContext,
    broadcast_id: int,
    *,
    page: int,
) -> str | None:
    if await _editable_broadcast(broadcast_id) is None:
        await _edit_current_broadcast_list(message, page=page)
        return "Отправка уведомления уже началась или оно недоступно."
    await state.set_state(AdminBroadcastForm.edited_schedule)
    await state.set_data({"broadcast_id": broadcast_id, "page": page})
    await message.edit_text(
        _schedule_prompt(prefix="Введите новую дату и время."),
        reply_markup=create_admin_form_cancel_keyboard(),
    )
    return None


async def _start_edit_recipients(
    message: Message,
    state: FSMContext,
    broadcast_id: int,
    *,
    page: int,
) -> str | None:
    if await _editable_broadcast(broadcast_id) is None:
        await _edit_current_broadcast_list(message, page=page)
        return "Отправка уведомления уже началась или оно недоступно."
    await state.set_state(AdminBroadcastForm.edited_recipients)
    await state.set_data({"broadcast_id": broadcast_id, "page": page})
    await message.edit_text(
        _recipients_prompt(prefix="Введите новый список дополнительных Telegram user ID."),
        reply_markup=create_admin_form_cancel_keyboard(),
    )
    return None


async def _show_delete_confirmation(message: Message, broadcast_id: int, *, page: int) -> str | None:
    broadcast = await _editable_broadcast(broadcast_id)
    if broadcast is None:
        await _edit_current_broadcast_list(message, page=page)
        return "Отправка уведомления уже началась или оно недоступно."
    await message.edit_text(
        "Удалить уведомление на "
        f"<b>{format_admin_broadcast_schedule(broadcast.scheduled_local_at, broadcast.timezone_offset_minutes)}</b>?",
        reply_markup=create_admin_broadcast_delete_keyboard(broadcast.id, page=page),
    )
    return None


async def _delete_broadcast(message: Message, broadcast_id: int, *, page: int) -> str | None:
    async with transaction():
        deleted = await soft_delete_admin_broadcast(broadcast_id)
    await _edit_current_broadcast_list(message, page=page)
    return "Уведомление удалено." if deleted else "Отправка уведомления уже началась или оно недоступно."


async def _editable_broadcast(broadcast_id: int) -> AdminBroadcast | None:
    broadcast = await get_admin_broadcast(broadcast_id)
    if broadcast is None or broadcast.status is not AdminBroadcastStatus.scheduled:
        return None
    return broadcast


async def _edit_current_broadcast_list(
    message: Message,
    *,
    page: int = 0,
    prefix: str | None = None,
) -> None:
    broadcasts = await get_scheduled_admin_broadcasts()
    if broadcasts:
        await _edit_with_broadcast_list(message, broadcasts, page=page, prefix=prefix)
    else:
        heading = "<b>Запланированных уведомлений нет.</b>"
        await message.edit_text(
            f"{prefix}\n\n{heading}" if prefix is not None else heading,
            reply_markup=create_admin_panel_keyboard(),
        )


async def _edit_with_broadcast_list(
    message: Message,
    broadcasts: list[AdminBroadcast],
    *,
    page: int = 0,
    prefix: str | None = None,
) -> None:
    heading = "<b>Запланированные уведомления</b>\n\nВыберите уведомление или создайте новое."
    await message.edit_text(
        f"{prefix}\n\n{heading}" if prefix is not None else heading,
        reply_markup=create_admin_broadcasts_keyboard(broadcasts, page=page),
    )


async def _answer_with_broadcast_list(message: Message, *, prefix: str, page: int = 0) -> None:
    broadcasts = await get_scheduled_admin_broadcasts()
    await message.answer(
        f"{prefix}\n\n<b>Запланированные уведомления</b>",
        reply_markup=create_admin_broadcasts_keyboard(broadcasts, page=page),
    )


async def _parse_schedule_message(message: Message) -> AdminBroadcastSchedule | None:
    if message.text is None:
        await message.answer(_schedule_error("Дата и время должны быть текстом."))
        return None
    try:
        return parse_admin_broadcast_schedule(message.text)
    except PastAdminBroadcastScheduleError:
        await message.answer(_schedule_error("Эта дата уже полностью прошла во всех часовых поясах."))
    except InvalidAdminBroadcastScheduleError:
        await message.answer(_schedule_error("Не удалось распознать дату, время или UTC-смещение."))
    return None


async def _parse_recipient_ids_message(message: Message) -> set[int] | None:
    if message.text is None:
        await message.answer(_recipients_error("Список ID должен быть текстом."))
        return None
    try:
        return parse_admin_broadcast_recipient_ids(message.text)
    except InvalidAdminBroadcastRecipientsError:
        await message.answer(_recipients_error("Не удалось распознать Telegram user ID."))
        return None


async def _create_broadcast_from_state(
    *,
    message: Message,
    state: FSMContext,
    recipient_ids: set[int],
) -> AdminBroadcast | None:
    data = await state.get_data()
    created_by_user_id = data.get("created_by_user_id")
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    scheduled_local_at = data.get("scheduled_local_at")
    timezone_offset_minutes = data.get("timezone_offset_minutes")
    if not (
        isinstance(created_by_user_id, int)
        and isinstance(source_chat_id, int)
        and isinstance(source_message_id, int)
        and isinstance(scheduled_local_at, datetime)
        and (timezone_offset_minutes is None or isinstance(timezone_offset_minutes, int))
    ):
        await state.clear()
        return None

    async with transaction():
        broadcast = await create_admin_broadcast(
            created_by_user_id=created_by_user_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            scheduled_local_at=scheduled_local_at,
            timezone_offset_minutes=timezone_offset_minutes,
        )
        await set_admin_broadcast_recipients(
            broadcast_id=broadcast.id,
            user_ids=recipient_ids,
        )
    await state.clear()
    return broadcast


async def _state_broadcast_id(state: FSMContext) -> int | None:
    broadcast_id = (await state.get_data()).get("broadcast_id")
    if not isinstance(broadcast_id, int):
        await state.clear()
        return None
    return broadcast_id


async def _state_page(state: FSMContext) -> int:
    page = (await state.get_data()).get("page", 0)
    return page if isinstance(page, int) else 0


def _unpack_broadcast_callback(data: str) -> AdminBroadcastPagedCallbackData:
    if data.startswith("abp:"):
        return AdminBroadcastPagedCallbackData.unpack(data)
    legacy = AdminBroadcastCallbackData.unpack(data)
    return AdminBroadcastPagedCallbackData(
        action=legacy.action,
        broadcast_id=legacy.broadcast_id,
    )


def _broadcast_details(
    broadcast: AdminBroadcast,
    *,
    extra_recipient_count: int,
    viewer_timezone_offset_minutes: int,
) -> str:
    countdown_label = "До отправки для вас" if broadcast.timezone_offset_minutes is None else "До отправки"
    countdown = format_admin_broadcast_countdown(
        broadcast.scheduled_local_at,
        broadcast.timezone_offset_minutes,
        viewer_timezone_offset_minutes=viewer_timezone_offset_minutes,
    )
    return (
        f"<b>Уведомление #{broadcast.id}</b>\n\n"
        "Дата и время: "
        f"<b>{format_admin_broadcast_schedule(broadcast.scheduled_local_at, broadcast.timezone_offset_minutes)}</b>\n"
        f"{countdown_label}: <b>{countdown}</b>\n"
        f"Исходное сообщение: <code>{broadcast.source_chat_id}/{broadcast.source_message_id}</code>\n"
        f"Дополнительных получателей: <b>{extra_recipient_count}</b>"
    )


def _schedule_prompt(*, prefix: str = "Введите дату и время отправки.") -> str:
    example_timezone = timezone(timedelta(minutes=DEFAULT_TIMEZONE_OFFSET_MINUTES))
    example_local_at = datetime.now(UTC).astimezone(example_timezone) + timedelta(hours=1)
    example = f"{example_local_at:%d.%m.%Y %H:%M} {format_timezone_offset(DEFAULT_TIMEZONE_OFFSET_MINUTES)}"
    return (
        f"{prefix}\n\n"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ [+ЧЧ:ММ]</code>.\n"
        "Без UTC-смещения сообщение придёт в это локальное время каждому получателю. "
        "Со смещением все получат его в один момент.\n"
        f"Например: <code>{example}</code>."
    )


def _schedule_error(reason: str) -> str:
    return f"{reason}\n\n{_schedule_prompt(prefix='Попробуйте ещё раз.')}"


def _recipients_prompt(*, prefix: str = "Укажите дополнительные Telegram user ID.") -> str:
    return (
        f"{prefix}\n\n"
        "Разделяйте ID пробелами, запятыми или переносами строк. "
        "Эти пользователи будут добавлены к общей активной аудитории только для этого уведомления. "
        f"Можно указать до {MAX_EXTRA_RECIPIENTS} ID. Отправьте <code>-</code>, чтобы очистить список."
    )


def _recipients_error(reason: str) -> str:
    return f"{reason}\n\n{_recipients_prompt(prefix='Попробуйте ещё раз.')}"


def _created_broadcast_text(broadcast: AdminBroadcast, extra_recipient_count: int, *, html: bool = True) -> str:
    formatted_schedule = format_admin_broadcast_schedule(
        broadcast.scheduled_local_at,
        broadcast.timezone_offset_minutes,
    )
    formatted_recipient_count = str(extra_recipient_count)
    if html:
        formatted_schedule = f"<b>{formatted_schedule}</b>"
        formatted_recipient_count = f"<b>{formatted_recipient_count}</b>"
    return (
        f"Уведомление запланировано на {formatted_schedule}. Дополнительных получателей: {formatted_recipient_count}."
    )


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None
    return callback.message

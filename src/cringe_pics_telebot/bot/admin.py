import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from cringe_pics_telebot.repositories.postgres import (
    create_admin_broadcast,
    get_admin_broadcast,
    get_scheduled_admin_broadcasts,
    soft_delete_admin_broadcast,
    transaction,
    update_admin_broadcast_message,
    update_admin_broadcast_schedule,
)
from cringe_pics_telebot.repositories.postgres.entities import AdminBroadcast, AdminBroadcastStatus
from cringe_pics_telebot.services.admin_broadcast_schedules import (
    AdminBroadcastSchedule,
    InvalidAdminBroadcastScheduleError,
    PastAdminBroadcastScheduleError,
    format_admin_broadcast_schedule,
    parse_admin_broadcast_schedule,
)

from .admin_access import IsAdministrator
from .admin_callback_data import AdminAction, AdminCallbackData
from .admin_keyboards import (
    create_admin_broadcast_delete_keyboard,
    create_admin_broadcast_keyboard,
    create_admin_broadcasts_keyboard,
    create_admin_form_cancel_keyboard,
    create_admin_panel_keyboard,
)

logger = logging.getLogger(__name__)

ADMIN_PANEL_BUTTON = "Админ-панель"

router = Router(name="admin")
router.message.filter(IsAdministrator())
router.callback_query.filter(IsAdministrator())


class AdminBroadcastForm(StatesGroup):
    new_message = State()
    new_schedule = State()
    edited_message = State()
    edited_schedule = State()


@router.message(Command("admin"))
@router.message(F.text == ADMIN_PANEL_BUTTON)
async def show_admin_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>Админ-панель</b>\n\nВыберите действие.",
        reply_markup=create_admin_panel_keyboard(),
    )


@router.callback_query(AdminCallbackData.filter())
async def handle_admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    if message is None or callback.data is None:
        await callback.answer("Сообщение панели недоступно.", show_alert=True)
        return

    callback_data = AdminCallbackData.unpack(callback.data)
    try:
        notice = await _dispatch_admin_callback(
            callback_data=callback_data,
            message=message,
            state=state,
        )
    except Exception:
        logger.exception("Failed to process admin callback %s", callback.data)
        await callback.answer("Не удалось выполнить действие.", show_alert=True)
        return

    await callback.answer(notice, show_alert=notice is not None)


@router.message(AdminBroadcastForm.new_message)
async def receive_new_broadcast_message(message: Message, state: FSMContext) -> None:
    await state.update_data(
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

    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    if not isinstance(source_chat_id, int) or not isinstance(source_message_id, int):
        await state.clear()
        await message.answer("Черновик потерян. Начните создание рассылки заново.")
        return

    assert message.from_user is not None
    async with transaction():
        broadcast = await create_admin_broadcast(
            created_by_user_id=message.from_user.id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            scheduled_local_at=schedule.local_at,
            timezone_offset_minutes=schedule.timezone_offset_minutes,
        )
    await state.clear()
    formatted_schedule = format_admin_broadcast_schedule(
        broadcast.scheduled_local_at,
        broadcast.timezone_offset_minutes,
    )
    await _answer_with_broadcast_list(
        message,
        prefix=f"Рассылка запланирована на <b>{formatted_schedule}</b>.",
    )


@router.message(AdminBroadcastForm.edited_message)
async def receive_edited_broadcast_message(message: Message, state: FSMContext) -> None:
    broadcast_id = await _state_broadcast_id(state)
    if broadcast_id is None:
        await message.answer("Черновик потерян. Откройте рассылку заново.")
        return

    async with transaction():
        updated = await update_admin_broadcast_message(
            broadcast_id=broadcast_id,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
    await state.clear()
    if not updated:
        await _answer_with_broadcast_list(message, prefix="Рассылка уже начала отправляться или недоступна.")
        return

    await _answer_with_broadcast_list(message, prefix="Сообщение рассылки обновлено.")


@router.message(AdminBroadcastForm.edited_schedule)
async def receive_edited_broadcast_schedule(message: Message, state: FSMContext) -> None:
    schedule = await _parse_schedule_message(message)
    if schedule is None:
        return

    broadcast_id = await _state_broadcast_id(state)
    if broadcast_id is None:
        await message.answer("Черновик потерян. Откройте рассылку заново.")
        return

    async with transaction():
        updated = await update_admin_broadcast_schedule(
            broadcast_id=broadcast_id,
            scheduled_local_at=schedule.local_at,
            timezone_offset_minutes=schedule.timezone_offset_minutes,
        )
    await state.clear()
    if not updated:
        await _answer_with_broadcast_list(message, prefix="Рассылка уже начала отправляться или недоступна.")
        return

    await _answer_with_broadcast_list(message, prefix="Дата и время рассылки обновлены.")


async def _dispatch_admin_callback(
    *,
    callback_data: AdminCallbackData,
    message: Message,
    state: FSMContext,
) -> str | None:
    match callback_data.action:
        case AdminAction.panel:
            await state.clear()
            await message.edit_text(
                "<b>Админ-панель</b>\n\nВыберите действие.",
                reply_markup=create_admin_panel_keyboard(),
            )
        case AdminAction.broadcasts:
            await state.clear()
            return await _show_broadcasts_or_start(message=message, state=state)
        case AdminAction.new_broadcast:
            await _start_new_broadcast(message=message, state=state)
        case AdminAction.edit_broadcast:
            await state.clear()
            return await _show_broadcast(message, callback_data.broadcast_id)
        case AdminAction.edit_message:
            return await _start_edit_message(message, state, callback_data.broadcast_id)
        case AdminAction.edit_schedule:
            return await _start_edit_schedule(message, state, callback_data.broadcast_id)
        case AdminAction.delete_broadcast:
            await state.clear()
            return await _show_delete_confirmation(message, callback_data.broadcast_id)
        case AdminAction.confirm_delete:
            await state.clear()
            return await _delete_broadcast(message, callback_data.broadcast_id)
        case AdminAction.cancel_form:
            await state.clear()
            await message.edit_text(
                "<b>Админ-панель</b>\n\nСоздание или редактирование отменено.",
                reply_markup=create_admin_panel_keyboard(),
            )
    return None


async def _show_broadcasts_or_start(*, message: Message, state: FSMContext) -> str | None:
    broadcasts = await get_scheduled_admin_broadcasts()
    if not broadcasts:
        await _start_new_broadcast(message=message, state=state)
        return None
    await _edit_with_broadcast_list(message, broadcasts)
    return None


async def _start_new_broadcast(*, message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminBroadcastForm.new_message)
    await message.edit_text(
        "<b>Новая рассылка</b>\n\nОтправьте сообщение, которое нужно разослать.",
        reply_markup=create_admin_form_cancel_keyboard(),
    )


async def _show_broadcast(message: Message, broadcast_id: int) -> str | None:
    broadcast = await _editable_broadcast(broadcast_id)
    if broadcast is None:
        await _edit_current_broadcast_list(message)
        return "Рассылка уже начала отправляться или недоступна."
    await message.edit_text(
        _broadcast_details(broadcast),
        reply_markup=create_admin_broadcast_keyboard(broadcast.id),
    )
    return None


async def _start_edit_message(message: Message, state: FSMContext, broadcast_id: int) -> str | None:
    if await _editable_broadcast(broadcast_id) is None:
        await _edit_current_broadcast_list(message)
        return "Рассылка уже начала отправляться или недоступна."
    await state.set_state(AdminBroadcastForm.edited_message)
    await state.set_data({"broadcast_id": broadcast_id})
    await message.edit_text(
        "Отправьте новое сообщение для рассылки.",
        reply_markup=create_admin_form_cancel_keyboard(),
    )
    return None


async def _start_edit_schedule(message: Message, state: FSMContext, broadcast_id: int) -> str | None:
    if await _editable_broadcast(broadcast_id) is None:
        await _edit_current_broadcast_list(message)
        return "Рассылка уже начала отправляться или недоступна."
    await state.set_state(AdminBroadcastForm.edited_schedule)
    await state.set_data({"broadcast_id": broadcast_id})
    await message.edit_text(
        _schedule_prompt(prefix="Введите новую дату и время."),
        reply_markup=create_admin_form_cancel_keyboard(),
    )
    return None


async def _show_delete_confirmation(message: Message, broadcast_id: int) -> str | None:
    broadcast = await _editable_broadcast(broadcast_id)
    if broadcast is None:
        await _edit_current_broadcast_list(message)
        return "Рассылка уже начала отправляться или недоступна."
    await message.edit_text(
        "Удалить рассылку на "
        f"<b>{format_admin_broadcast_schedule(broadcast.scheduled_local_at, broadcast.timezone_offset_minutes)}</b>?",
        reply_markup=create_admin_broadcast_delete_keyboard(broadcast.id),
    )
    return None


async def _delete_broadcast(message: Message, broadcast_id: int) -> str | None:
    async with transaction():
        deleted = await soft_delete_admin_broadcast(broadcast_id)
    await _edit_current_broadcast_list(message)
    return "Рассылка удалена." if deleted else "Рассылка уже начала отправляться или недоступна."


async def _editable_broadcast(broadcast_id: int) -> AdminBroadcast | None:
    broadcast = await get_admin_broadcast(broadcast_id)
    if broadcast is None or broadcast.status is not AdminBroadcastStatus.scheduled:
        return None
    return broadcast


async def _edit_current_broadcast_list(message: Message) -> None:
    broadcasts = await get_scheduled_admin_broadcasts()
    if broadcasts:
        await _edit_with_broadcast_list(message, broadcasts)
    else:
        await message.edit_text(
            "<b>Запланированных рассылок нет.</b>",
            reply_markup=create_admin_panel_keyboard(),
        )


async def _edit_with_broadcast_list(message: Message, broadcasts: list[AdminBroadcast]) -> None:
    await message.edit_text(
        "<b>Запланированные рассылки</b>\n\nВыберите рассылку или создайте новую.",
        reply_markup=create_admin_broadcasts_keyboard(broadcasts),
    )


async def _answer_with_broadcast_list(message: Message, *, prefix: str) -> None:
    broadcasts = await get_scheduled_admin_broadcasts()
    await message.answer(
        f"{prefix}\n\n<b>Запланированные рассылки</b>",
        reply_markup=create_admin_broadcasts_keyboard(broadcasts),
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


async def _state_broadcast_id(state: FSMContext) -> int | None:
    broadcast_id = (await state.get_data()).get("broadcast_id")
    if not isinstance(broadcast_id, int):
        await state.clear()
        return None
    return broadcast_id


def _broadcast_details(broadcast: AdminBroadcast) -> str:
    return (
        f"<b>Рассылка #{broadcast.id}</b>\n\n"
        "Дата и время: "
        f"<b>{format_admin_broadcast_schedule(broadcast.scheduled_local_at, broadcast.timezone_offset_minutes)}</b>\n"
        f"Исходное сообщение: <code>{broadcast.source_chat_id}/{broadcast.source_message_id}</code>"
    )


def _schedule_prompt(*, prefix: str = "Введите дату и время отправки.") -> str:
    return (
        f"{prefix}\n\n"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ [+ЧЧ:ММ]</code>.\n"
        "Без UTC-смещения сообщение придёт в это локальное время каждому получателю. "
        "Со смещением все получат его в один момент, например: "
        "<code>20.08.2026 10:00 +07:00</code>."
    )


def _schedule_error(reason: str) -> str:
    return f"{reason}\n\n{_schedule_prompt(prefix='Попробуйте ещё раз.')}"


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None
    return callback.message

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from .admin_access import IsAdministrator
from .admin_keyboards import create_admin_panel_keyboard
from .admin_panel_callback_data import AdminPanelCallbackData

ADMIN_PANEL_BUTTON = "Админ-панель"

router = Router(name="admin_panel")
router.message.filter(IsAdministrator())
router.callback_query.filter(IsAdministrator())


@router.message(Command("admin"))
@router.message(F.text == ADMIN_PANEL_BUTTON)
async def show_admin_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>Админ-панель</b>\n\nВыберите действие.",
        reply_markup=create_admin_panel_keyboard(),
    )


@router.callback_query(AdminPanelCallbackData.filter())
async def handle_admin_panel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    if message is None:
        await callback.answer("Сообщение панели недоступно.", show_alert=True)
        return

    await state.clear()
    await message.edit_text(
        "<b>Админ-панель</b>\n\nВыберите действие.",
        reply_markup=create_admin_panel_keyboard(),
    )
    await callback.answer()


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None
    return callback.message

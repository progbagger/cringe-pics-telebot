import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from cringe_pics_telebot.services.media_sync import MediaSyncSummary, synchronize_media_catalog

from .admin_access import IsAdministrator
from .admin_keyboards import create_admin_media_sync_keyboard, create_admin_panel_keyboard
from .admin_panel_callback_data import AdminPanelAction, AdminPanelCallbackData

ADMIN_PANEL_BUTTON = "Админ-панель"

logger = logging.getLogger(__name__)

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
    if message is None or callback.data is None:
        await callback.answer("Сообщение панели недоступно.", show_alert=True)
        return

    callback_data = AdminPanelCallbackData.unpack(callback.data)
    match callback_data.action:
        case AdminPanelAction.panel:
            await state.clear()
            await message.edit_text(
                "<b>Админ-панель</b>\n\nВыберите действие.",
                reply_markup=create_admin_panel_keyboard(),
            )
            await callback.answer()
        case AdminPanelAction.synchronize_media:
            await callback.answer()
            await state.clear()
            await _synchronize_media(message)


async def _synchronize_media(message: Message) -> None:
    await message.edit_text("<b>Синхронизация медиа началась</b>\n\nЭто может занять некоторое время.")
    try:
        summary = await synchronize_media_catalog()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to manually synchronize media catalog")
        await message.edit_text(
            "<b>Не удалось синхронизировать медиа</b>\n\nПопробуйте повторить позже.",
            reply_markup=create_admin_media_sync_keyboard(),
        )
        return

    await message.edit_text(
        _media_sync_summary_text(summary),
        reply_markup=create_admin_media_sync_keyboard(),
    )


def _media_sync_summary_text(summary: MediaSyncSummary) -> str:
    if not summary.acquired:
        return "<b>Синхронизация медиа уже выполняется</b>\n\nНовый запуск не начат."

    status = "завершена частично" if summary.failed else "завершена"
    return (
        f"<b>Синхронизация медиа {status}</b>\n\n"
        f"Обработано категорий: <b>{summary.categories}</b>\n"
        f"Категорий с ошибками: <b>{summary.failed}</b>\n"
        f"Найдено медиа: <b>{summary.discovered}</b>\n"
        f"Создано записей: <b>{summary.created}</b>\n"
        f"Изменено записей: <b>{summary.changed}</b>\n"
        f"Повторно активировано медиа: <b>{summary.reactivated}</b>\n"
        f"Деактивировано отсутствующее медиа: <b>{summary.deactivated}</b>"
    )


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None
    return callback.message

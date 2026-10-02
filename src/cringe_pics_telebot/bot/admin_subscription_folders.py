import logging
from html import escape

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from cringe_pics_telebot.entities.admin_subscription_folders import AdminSubscriptionFolderEditor
from cringe_pics_telebot.services.admin_subscription_folders import (
    AdminSubscriptionFolderCategoryUnavailableError,
    AdminSubscriptionFolderMembershipConflictError,
    AdminSubscriptionFolderNameConflictError,
    AdminSubscriptionFolderUnavailableError,
    InvalidAdminSubscriptionFolderNameError,
    create_admin_subscription_folder,
    delete_admin_subscription_folder,
    get_admin_subscription_folder_editor,
    get_admin_subscription_folders,
    rename_admin_subscription_folder,
    toggle_admin_subscription_folder_category,
)

from .admin_access import IsAdministrator
from .admin_keyboards import (
    create_admin_subscription_folder_delete_keyboard,
    create_admin_subscription_folder_form_cancel_keyboard,
    create_admin_subscription_folder_keyboard,
    create_admin_subscription_folders_keyboard,
)
from .admin_subscription_folder_callback_data import (
    AdminSubscriptionFolderAction,
    AdminSubscriptionFolderCallbackData,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_subscription_folders")
router.message.filter(IsAdministrator())
router.callback_query.filter(IsAdministrator())


class AdminSubscriptionFolderCreationForm(StatesGroup):
    name = State()


class AdminSubscriptionFolderRenameForm(StatesGroup):
    name = State()


@router.callback_query(AdminSubscriptionFolderCallbackData.filter())
async def handle_admin_subscription_folder_callback(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    if message is None or callback.data is None:
        await callback.answer("Сообщение панели недоступно.", show_alert=True)
        return

    callback_data = AdminSubscriptionFolderCallbackData.unpack(callback.data)
    try:
        notice = await _dispatch_admin_subscription_folder_callback(
            callback_data=callback_data,
            message=message,
            state=state,
        )
    except Exception:
        logger.exception("Failed to process admin subscription folder callback %s", callback.data)
        await callback.answer("Не удалось выполнить действие.", show_alert=True)
        return

    await callback.answer(notice, show_alert=notice is not None)


@router.message(AdminSubscriptionFolderCreationForm.name)
async def receive_admin_subscription_folder_name(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(
            _folder_name_error("Название папки должно быть текстом."),
            reply_markup=create_admin_subscription_folder_form_cancel_keyboard(
                folder_page=await _state_int(state, "folder_page"),
            ),
        )
        return

    folder_page = await _state_int(state, "folder_page")
    try:
        folder = await create_admin_subscription_folder(message.text)
    except InvalidAdminSubscriptionFolderNameError:
        await message.answer(
            _folder_name_error("Название папки не должно быть пустым."),
            reply_markup=create_admin_subscription_folder_form_cancel_keyboard(folder_page=folder_page),
        )
        return
    except AdminSubscriptionFolderNameConflictError:
        await message.answer(
            _folder_name_error("Папка с таким названием уже существует."),
            reply_markup=create_admin_subscription_folder_form_cancel_keyboard(folder_page=folder_page),
        )
        return

    await state.clear()
    editor = await get_admin_subscription_folder_editor(folder.id)
    if editor is None:
        await _answer_folders(message, prefix="Папка была создана, но больше недоступна.")
        return

    await message.answer(
        f"Папка создана.\n\n{_folder_text(editor)}",
        reply_markup=create_admin_subscription_folder_keyboard(editor, folder_page=folder_page),
    )


@router.message(AdminSubscriptionFolderRenameForm.name)
async def receive_admin_subscription_folder_rename(message: Message, state: FSMContext) -> None:
    folder_id = await _state_int(state, "folder_id")
    folder_page = await _state_int(state, "folder_page")
    category_page = await _state_int(state, "category_page")
    if folder_id <= 0:
        await state.clear()
        await _answer_folders(message, prefix="Черновик переименования потерян.")
        return

    cancel_keyboard = create_admin_subscription_folder_form_cancel_keyboard(
        folder_id=folder_id,
        folder_page=folder_page,
        category_page=category_page,
    )
    if message.text is None:
        await message.answer(
            _folder_name_error("Название папки должно быть текстом."),
            reply_markup=cancel_keyboard,
        )
        return

    try:
        await rename_admin_subscription_folder(folder_id, message.text)
    except InvalidAdminSubscriptionFolderNameError:
        await message.answer(
            _folder_name_error("Название папки не должно быть пустым."),
            reply_markup=cancel_keyboard,
        )
        return
    except AdminSubscriptionFolderNameConflictError:
        await message.answer(
            _folder_name_error("Папка с таким названием уже существует."),
            reply_markup=cancel_keyboard,
        )
        return
    except AdminSubscriptionFolderUnavailableError:
        await state.clear()
        await _answer_folders(message, prefix="Папка больше недоступна.")
        return

    await state.clear()
    editor = await get_admin_subscription_folder_editor(folder_id)
    if editor is None:
        await _answer_folders(message, prefix="Папка переименована, но больше недоступна.")
        return

    await message.answer(
        f"Папка переименована.\n\n{_folder_text(editor)}",
        reply_markup=create_admin_subscription_folder_keyboard(
            editor,
            folder_page=folder_page,
            category_page=category_page,
        ),
    )


async def _dispatch_admin_subscription_folder_callback(
    *,
    callback_data: AdminSubscriptionFolderCallbackData,
    message: Message,
    state: FSMContext,
) -> str | None:
    match callback_data.action:
        case AdminSubscriptionFolderAction.folders:
            await state.clear()
            await _show_folders(message, page=callback_data.folder_page)
        case AdminSubscriptionFolderAction.folder:
            await state.clear()
            return await _show_folder(
                message,
                callback_data.folder_id,
                folder_page=callback_data.folder_page,
                category_page=callback_data.category_page,
            )
        case AdminSubscriptionFolderAction.create:
            await _start_create_folder(message, state, folder_page=callback_data.folder_page)
        case AdminSubscriptionFolderAction.rename:
            return await _start_rename_folder(message, state, callback_data)
        case AdminSubscriptionFolderAction.toggle_category:
            await state.clear()
            return await _toggle_category(message, callback_data)
        case AdminSubscriptionFolderAction.delete:
            await state.clear()
            return await _show_delete_confirmation(message, callback_data)
        case AdminSubscriptionFolderAction.confirm_delete:
            await state.clear()
            return await _delete_folder(message, callback_data)
        case AdminSubscriptionFolderAction.cancel_form:
            await state.clear()
            if callback_data.folder_id:
                return await _show_folder(
                    message,
                    callback_data.folder_id,
                    folder_page=callback_data.folder_page,
                    category_page=callback_data.category_page,
                )
            await _show_folders(message, page=callback_data.folder_page)
    return None


async def _show_folders(message: Message, *, page: int = 0, prefix: str | None = None) -> None:
    folders = await get_admin_subscription_folders()
    heading = "<b>Управление папками</b>\n\nВыберите папку или создайте новую."
    await message.edit_text(
        f"{prefix}\n\n{heading}" if prefix is not None else heading,
        reply_markup=create_admin_subscription_folders_keyboard(folders, page=page),
    )


async def _answer_folders(message: Message, *, prefix: str | None = None) -> None:
    folders = await get_admin_subscription_folders()
    heading = "<b>Управление папками</b>\n\nВыберите папку или создайте новую."
    await message.answer(
        f"{prefix}\n\n{heading}" if prefix is not None else heading,
        reply_markup=create_admin_subscription_folders_keyboard(folders),
    )


async def _show_folder(
    message: Message,
    folder_id: int,
    *,
    folder_page: int,
    category_page: int,
) -> str | None:
    editor = await get_admin_subscription_folder_editor(folder_id)
    if editor is None:
        await _show_folders(message, page=folder_page)
        return "Папка больше недоступна."

    await message.edit_text(
        _folder_text(editor),
        reply_markup=create_admin_subscription_folder_keyboard(
            editor,
            folder_page=folder_page,
            category_page=category_page,
        ),
    )
    return None


async def _start_create_folder(message: Message, state: FSMContext, *, folder_page: int) -> None:
    await state.clear()
    await state.set_state(AdminSubscriptionFolderCreationForm.name)
    await state.set_data({"folder_page": folder_page})
    await message.edit_text(
        "<b>Создание папки</b>\n\nВведите название папки.",
        reply_markup=create_admin_subscription_folder_form_cancel_keyboard(folder_page=folder_page),
    )


async def _start_rename_folder(
    message: Message,
    state: FSMContext,
    callback_data: AdminSubscriptionFolderCallbackData,
) -> str | None:
    editor = await get_admin_subscription_folder_editor(callback_data.folder_id)
    if editor is None:
        await _show_folders(message, page=callback_data.folder_page)
        return "Папка больше недоступна."

    await state.set_state(AdminSubscriptionFolderRenameForm.name)
    await state.set_data(
        {
            "folder_id": callback_data.folder_id,
            "folder_page": callback_data.folder_page,
            "category_page": callback_data.category_page,
        }
    )
    await message.edit_text(
        f"<b>Переименование папки «{escape(editor.folder.name)}»</b>\n\nВведите новое название.",
        reply_markup=create_admin_subscription_folder_form_cancel_keyboard(
            folder_id=callback_data.folder_id,
            folder_page=callback_data.folder_page,
            category_page=callback_data.category_page,
        ),
    )
    return None


async def _toggle_category(
    message: Message,
    callback_data: AdminSubscriptionFolderCallbackData,
) -> str:
    try:
        is_member = await toggle_admin_subscription_folder_category(
            folder_id=callback_data.folder_id,
            category_id=callback_data.category_id,
        )
    except AdminSubscriptionFolderMembershipConflictError as error:
        await _show_folder(
            message,
            callback_data.folder_id,
            folder_page=callback_data.folder_page,
            category_page=callback_data.category_page,
        )
        return f"Сначала удалите категорию из папки «{error.folder.name}»."
    except AdminSubscriptionFolderUnavailableError:
        await _show_folders(message, page=callback_data.folder_page)
        return "Папка больше недоступна."
    except AdminSubscriptionFolderCategoryUnavailableError:
        notice = await _show_folder(
            message,
            callback_data.folder_id,
            folder_page=callback_data.folder_page,
            category_page=callback_data.category_page,
        )
        return notice or "Категория больше недоступна."

    await _show_folder(
        message,
        callback_data.folder_id,
        folder_page=callback_data.folder_page,
        category_page=callback_data.category_page,
    )
    return "Категория добавлена в папку." if is_member else "Категория удалена из папки."


async def _show_delete_confirmation(
    message: Message,
    callback_data: AdminSubscriptionFolderCallbackData,
) -> str | None:
    editor = await get_admin_subscription_folder_editor(callback_data.folder_id)
    if editor is None:
        await _show_folders(message, page=callback_data.folder_page)
        return "Папка больше недоступна."

    await message.edit_text(
        f"<b>Удалить папку «{escape(editor.folder.name)}»?</b>\n\n"
        f"Категорий в папке: <b>{editor.member_count}</b>. "
        "Категории, подписки и расписания сохранятся.",
        reply_markup=create_admin_subscription_folder_delete_keyboard(
            folder_id=callback_data.folder_id,
            folder_page=callback_data.folder_page,
            category_page=callback_data.category_page,
        ),
    )
    return None


async def _delete_folder(
    message: Message,
    callback_data: AdminSubscriptionFolderCallbackData,
) -> str:
    try:
        folder = await delete_admin_subscription_folder(callback_data.folder_id)
    except AdminSubscriptionFolderUnavailableError:
        await _show_folders(message, page=callback_data.folder_page)
        return "Папка больше недоступна."

    await _show_folders(
        message,
        page=callback_data.folder_page,
        prefix=f"Папка «{escape(folder.name)}» удалена.",
    )
    return "Папка удалена."


def _folder_text(editor: AdminSubscriptionFolderEditor) -> str:
    return (
        f"<b>Папка «{escape(editor.folder.name)}»</b>\n\n"
        f"Категорий в папке: <b>{editor.member_count}</b>. "
        "Нажмите на категорию, чтобы добавить её или удалить из папки."
    )


def _folder_name_error(message: str) -> str:
    return f"<b>Не удалось сохранить папку</b>\n\n{message}\n\nВведите другое название."


async def _state_int(state: FSMContext, key: str) -> int:
    value = (await state.get_data()).get(key)
    return value if isinstance(value, int) else 0


def _callback_message(callback: CallbackQuery) -> Message | None:
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return None
    return callback.message

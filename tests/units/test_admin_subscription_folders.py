import pytest
from aiogram.types import InlineKeyboardButton
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.bot.admin_keyboards import (
    create_admin_subscription_folder_keyboard,
    create_admin_subscription_folders_keyboard,
)
from cringe_pics_telebot.bot.admin_subscription_folder_callback_data import (
    AdminSubscriptionFolderAction,
    AdminSubscriptionFolderCallbackData,
)
from cringe_pics_telebot.entities.admin_subscription_folders import (
    AdminSubscriptionFolderCategory,
    AdminSubscriptionFolderEditor,
)
from cringe_pics_telebot.entities.subscription_menu import SubscriptionFolder
from cringe_pics_telebot.services.admin_subscription_folders import (
    InvalidAdminSubscriptionFolderNameError,
    parse_admin_subscription_folder_name,
)


def test_admin_subscription_folder_list_sorts_folders_before_actions() -> None:
    keyboard = create_admin_subscription_folders_keyboard(
        (
            SubscriptionFolder(id=2, name="Январь"),
            SubscriptionFolder(id=1, name="Апрель"),
        )
    )

    assert_that(
        _button_texts(keyboard.inline_keyboard),
        equal_to(["📁 Апрель", "📁 Январь", "Создать папку", "Назад"]),
    )
    callback = AdminSubscriptionFolderCallbackData.unpack(keyboard.inline_keyboard[0][0].callback_data or "")
    assert_that(callback.action, equal_to(AdminSubscriptionFolderAction.folder))
    assert_that(callback.folder_id, equal_to(1))


def test_admin_subscription_folder_editor_marks_every_membership_location() -> None:
    target = SubscriptionFolder(id=7, name="Праздники")
    other = SubscriptionFolder(id=8, name="Сезоны")
    editor = AdminSubscriptionFolderEditor(
        folder=target,
        categories=(
            AdminSubscriptionFolderCategory(id=1, name="/free", folder=None),
            AdminSubscriptionFolderCategory(id=2, name="/member", folder=target),
            AdminSubscriptionFolderCategory(id=3, name="/other", folder=other),
        ),
    )

    keyboard = create_admin_subscription_folder_keyboard(editor, folder_page=2, category_page=0)

    assert_that(
        _button_texts(keyboard.inline_keyboard),
        equal_to(
            [
                "❌ /free",
                "✅ /member",
                "📁 /other — Сезоны",
                "Переименовать",
                "Удалить папку",
                "Назад",
            ]
        ),
    )
    callback = AdminSubscriptionFolderCallbackData.unpack(keyboard.inline_keyboard[2][0].callback_data or "")
    assert_that(callback.action, equal_to(AdminSubscriptionFolderAction.toggle_category))
    assert_that(callback.folder_id, equal_to(7))
    assert_that(callback.category_id, equal_to(3))
    assert_that(callback.folder_page, equal_to(2))


def test_admin_subscription_folder_editor_keeps_actions_on_every_page() -> None:
    target = SubscriptionFolder(id=7, name="Большая папка")
    editor = AdminSubscriptionFolderEditor(
        folder=target,
        categories=tuple(
            AdminSubscriptionFolderCategory(id=index, name=f"/category-{index:02d}", folder=None) for index in range(9)
        ),
    )

    first_page = create_admin_subscription_folder_keyboard(editor, category_page=0)
    assert_that(
        _button_texts(first_page.inline_keyboard)[-4:],
        equal_to(["Переименовать", "Удалить папку", "Назад", ">"]),
    )
    next_callback = AdminSubscriptionFolderCallbackData.unpack(_button_callback_data(first_page.inline_keyboard, ">"))
    assert_that(next_callback.action, equal_to(AdminSubscriptionFolderAction.folder))
    assert_that(next_callback.category_page, equal_to(1))

    second_page = create_admin_subscription_folder_keyboard(editor, category_page=1)
    assert_that(
        _button_texts(second_page.inline_keyboard),
        equal_to(["❌ /category-08", "Переименовать", "Удалить папку", "Назад", "<"]),
    )


def test_admin_subscription_folder_callback_fits_telegram_limit() -> None:
    callback = AdminSubscriptionFolderCallbackData(
        action=AdminSubscriptionFolderAction.toggle_category,
        folder_id=9_223_372_036_854_775_807,
        category_id=9_223_372_036_854_775_807,
        folder_page=999_999,
        category_page=999_999,
    ).pack()

    assert len(callback.encode()) <= 64


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Праздники ", "Праздники"),
        ("Новый год", "Новый год"),
    ],
)
def test_parse_admin_subscription_folder_name_strips_outer_whitespace(value: str, expected: str) -> None:
    assert_that(parse_admin_subscription_folder_name(value), equal_to(expected))


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_parse_admin_subscription_folder_name_rejects_empty_value(value: str) -> None:
    with pytest.raises(InvalidAdminSubscriptionFolderNameError):
        parse_admin_subscription_folder_name(value)


def _button_texts(rows: list[list[InlineKeyboardButton]]) -> list[str]:
    return [button.text for row in rows for button in row]


def _button_callback_data(rows: list[list[InlineKeyboardButton]], text: str) -> str:
    return next(button.callback_data or "" for row in rows for button in row if button.text == text)

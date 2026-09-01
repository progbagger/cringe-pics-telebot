from datetime import time

import pytest
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.bot.admin_category_callback_data import AdminCategoryAction, AdminCategoryCallbackData
from cringe_pics_telebot.bot.admin_keyboards import create_admin_category_weekdays_keyboard
from cringe_pics_telebot.services.admin_categories import (
    InvalidAdminCategoryNameError,
    InvalidAdminCategoryPathError,
    InvalidAdminCategoryTimeError,
    parse_admin_category_name,
    parse_admin_category_path,
    parse_admin_category_time,
)


def test_parse_admin_category_name_trims_whitespace() -> None:
    assert_that(parse_admin_category_name("  /afternoon  "), equal_to("/afternoon"))


@pytest.mark.parametrize("value", ["", "  ", "\n\t"])
def test_parse_admin_category_name_rejects_empty_value(value: str) -> None:
    with pytest.raises(InvalidAdminCategoryNameError):
        parse_admin_category_name(value)


def test_parse_admin_category_path_trims_whitespace() -> None:
    assert_that(parse_admin_category_path("  afternoon/images  "), equal_to("afternoon/images"))


@pytest.mark.parametrize("value", ["", "  ", "\n\t"])
def test_parse_admin_category_path_rejects_empty_value(value: str) -> None:
    with pytest.raises(InvalidAdminCategoryPathError):
        parse_admin_category_path(value)


def test_parse_admin_category_time_returns_naive_time() -> None:
    parsed = parse_admin_category_time(" 09:05 ")

    assert_that(parsed, equal_to(time(9, 5)))
    assert parsed.tzinfo is None


@pytest.mark.parametrize(
    "value",
    ["", "9:05", "09:5", "24:00", "23:60", "09:05:00", "+09:05", "09:05 +07:00"],
)
def test_parse_admin_category_time_rejects_non_hh_mm_value(value: str) -> None:
    with pytest.raises(InvalidAdminCategoryTimeError):
        parse_admin_category_time(value)


def test_admin_category_weekdays_keyboard_marks_selection_and_keeps_callbacks_short() -> None:
    markup = create_admin_category_weekdays_keyboard((1, 3, 5))
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert_that(
        [button.text for button in buttons],
        equal_to(["✅ Пн", "Вт", "✅ Ср", "Чт", "✅ Пт", "Сб", "Вс", "Готово", "Каждый день", "Отмена"]),
    )
    callback_values = [button.callback_data for button in buttons]
    assert all(value is not None and len(value.encode()) <= 64 for value in callback_values)
    monday = AdminCategoryCallbackData.unpack(callback_values[0] or "")
    assert monday.action is AdminCategoryAction.toggle_weekday
    assert monday.weekday == 1

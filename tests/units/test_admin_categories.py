from collections.abc import Callable
from datetime import time

import pytest
from aiogram.types import InlineKeyboardMarkup
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.bot.admin_category_callback_data import AdminCategoryAction, AdminCategoryCallbackData
from cringe_pics_telebot.bot.admin_keyboards import (
    create_admin_category_keyboard,
    create_admin_category_schedule_kind_keyboard,
    create_admin_category_schedule_mode_keyboard,
    create_admin_category_weekdays_keyboard,
)
from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.subscription_schedule import SubscriptionScheduleKind
from cringe_pics_telebot.services.admin_categories import (
    InvalidAdminCategoryDateError,
    InvalidAdminCategoryNameError,
    InvalidAdminCategoryPathError,
    InvalidAdminCategoryTimeError,
    parse_admin_category_date,
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [("01.01", AnnualDate(month=1, day=1)), (" 29.02 ", AnnualDate(month=2, day=29))],
)
def test_parse_admin_category_date_returns_annual_date(value: str, expected: AnnualDate) -> None:
    assert_that(parse_admin_category_date(value), equal_to(expected))


@pytest.mark.parametrize(
    "value",
    ["", "1.01", "01.1", "01.01.2026", "2026-01-01", "00.01", "31.04", "30.02", "text"],
)
def test_parse_admin_category_date_rejects_non_dd_mm_or_impossible_value(value: str) -> None:
    with pytest.raises(InvalidAdminCategoryDateError):
        parse_admin_category_date(value)


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


@pytest.mark.parametrize(
    "markup_factory",
    [create_admin_category_schedule_mode_keyboard, create_admin_category_schedule_kind_keyboard],
)
def test_admin_category_schedule_keyboards_keep_callbacks_short(
    markup_factory: Callable[[], InlineKeyboardMarkup],
) -> None:
    markup = markup_factory()
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert all(button.callback_data is not None and len(button.callback_data.encode()) <= 64 for button in buttons)


@pytest.mark.parametrize("schedule_kind", list(SubscriptionScheduleKind))
def test_admin_category_card_keeps_new_paged_callbacks_short(schedule_kind: SubscriptionScheduleKind) -> None:
    markup = create_admin_category_keyboard(
        9_223_372_036_854_775_807,
        has_aliases=True,
        has_schedule=True,
        is_active=True,
        schedule_kind=schedule_kind,
        page=999_999,
    )
    callback_values = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert all(value is not None and len(value.encode()) <= 64 for value in callback_values)

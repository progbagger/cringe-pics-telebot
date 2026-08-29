from datetime import time

import pytest
from hamcrest import assert_that, equal_to

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

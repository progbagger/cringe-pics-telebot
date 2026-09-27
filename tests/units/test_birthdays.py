import pytest
from hamcrest import assert_that, equal_to, none

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.services.birthdays import InvalidBirthdayError, parse_birthday, parse_birthday_command


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01.01", AnnualDate(month=1, day=1)),
        (" 31.12 ", AnnualDate(month=12, day=31)),
        ("29.02", AnnualDate(month=2, day=29)),
    ],
)
def test_parse_birthday_accepts_valid_strict_dates(value: str, expected: AnnualDate) -> None:
    assert_that(parse_birthday(value), equal_to(expected))


@pytest.mark.parametrize(
    "value",
    [
        "1.01",
        "01.1",
        "31.04",
        "29.13",
        "00.12",
        "01.01.2000",
        "01.01 extra",
        "clear",
        "",
    ],
)
def test_parse_birthday_rejects_invalid_or_non_date_values(value: str) -> None:
    with pytest.raises(InvalidBirthdayError):
        parse_birthday(value)


def test_parse_birthday_command_recognizes_clear_case_insensitively() -> None:
    assert_that(parse_birthday_command(" CLEAR "), none())

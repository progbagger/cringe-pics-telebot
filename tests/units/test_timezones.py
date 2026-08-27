import pytest
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.services.timezones import (
    InvalidTimezoneOffsetError,
    format_timezone_offset,
    parse_timezone_offset,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("+07:00", 420),
        (" +04:30 ", 270),
        ("+00:00", 0),
        ("-00:30", -30),
        ("-12:00", -720),
        ("+14:00", 840),
    ],
)
def test_parse_timezone_offset(value: str, expected: int) -> None:
    assert_that(parse_timezone_offset(value), equal_to(expected))


@pytest.mark.parametrize(
    "value",
    [
        "04:00",
        "+4:00",
        "+04",
        "+04:60",
        "-12:01",
        "+14:01",
        "+24:00",
        "Europe/Moscow",
    ],
)
def test_parse_timezone_offset_rejects_invalid_values(value: str) -> None:
    with pytest.raises(InvalidTimezoneOffsetError):
        parse_timezone_offset(value)


@pytest.mark.parametrize(
    ("offset_minutes", "expected"),
    [
        (420, "+07:00"),
        (270, "+04:30"),
        (0, "+00:00"),
        (-30, "-00:30"),
        (-720, "-12:00"),
        (840, "+14:00"),
    ],
)
def test_format_timezone_offset(offset_minutes: int, expected: str) -> None:
    assert_that(format_timezone_offset(offset_minutes), equal_to(expected))

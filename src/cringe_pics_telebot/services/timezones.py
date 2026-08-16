import re

from cringe_pics_telebot.repositories.postgres import (
    get_user_timezone_offset as get_user_timezone_offset_from_pg,
)
from cringe_pics_telebot.repositories.postgres import (
    set_user_timezone_offset as set_user_timezone_offset_in_pg,
)
from cringe_pics_telebot.repositories.postgres import transaction

DEFAULT_TIMEZONE_OFFSET_MINUTES = 7 * 60
MIN_TIMEZONE_OFFSET_MINUTES = -12 * 60
MAX_TIMEZONE_OFFSET_MINUTES = 14 * 60

_TIMEZONE_OFFSET_PATTERN = re.compile(r"(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})")


class InvalidTimezoneOffsetError(ValueError): ...


def parse_timezone_offset(value: str) -> int:
    match = _TIMEZONE_OFFSET_PATTERN.fullmatch(value.strip())
    if match is None:
        raise InvalidTimezoneOffsetError("Timezone offset must use the format ±HH:MM")

    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    if minutes >= 60:
        raise InvalidTimezoneOffsetError("Timezone offset minutes must be less than 60")

    sign = -1 if match.group("sign") == "-" else 1
    offset_minutes = sign * (hours * 60 + minutes)
    _validate_timezone_offset(offset_minutes)
    return offset_minutes


def format_timezone_offset(offset_minutes: int) -> str:
    _validate_timezone_offset(offset_minutes)
    sign = "-" if offset_minutes < 0 else "+"
    hours, minutes = divmod(abs(offset_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


async def get_user_timezone_offset(user_id: int) -> int:
    offset_minutes = await get_user_timezone_offset_from_pg(user_id)
    return DEFAULT_TIMEZONE_OFFSET_MINUTES if offset_minutes is None else offset_minutes


async def set_user_timezone_offset(*, user_id: int, offset_minutes: int) -> None:
    _validate_timezone_offset(offset_minutes)
    async with transaction():
        await set_user_timezone_offset_in_pg(
            user_id=user_id,
            timezone_offset_minutes=offset_minutes,
        )


def _validate_timezone_offset(offset_minutes: int) -> None:
    if not MIN_TIMEZONE_OFFSET_MINUTES <= offset_minutes <= MAX_TIMEZONE_OFFSET_MINUTES:
        raise InvalidTimezoneOffsetError("Timezone offset must be between -12:00 and +14:00")

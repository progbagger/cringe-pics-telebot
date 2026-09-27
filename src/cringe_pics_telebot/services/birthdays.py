import re

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.user_birthday import UserBirthday, UserBirthdaySource
from cringe_pics_telebot.repositories.postgres import (
    clear_user_birthday as clear_user_birthday_in_pg,
)
from cringe_pics_telebot.repositories.postgres import (
    get_user_birthday_details as get_user_birthday_details_from_pg,
)
from cringe_pics_telebot.repositories.postgres import set_user_birthday as set_user_birthday_in_pg
from cringe_pics_telebot.repositories.postgres import transaction

_BIRTHDAY_PATTERN = re.compile(r"\d{2}\.\d{2}")


class InvalidBirthdayError(ValueError): ...


def parse_birthday(value: str) -> AnnualDate:
    normalized = value.strip()
    if _BIRTHDAY_PATTERN.fullmatch(normalized) is None:
        raise InvalidBirthdayError("Birthday must use DD.MM")

    day, month = (int(part) for part in normalized.split("."))
    try:
        return AnnualDate(month=month, day=day)
    except ValueError as error:
        raise InvalidBirthdayError("Birthday must contain a valid day and month") from error


def parse_birthday_command(value: str) -> AnnualDate | None:
    if value.strip().casefold() == "clear":
        return None

    return parse_birthday(value)


async def get_user_birthday(user_id: int) -> UserBirthday | None:
    return await get_user_birthday_details_from_pg(user_id)


async def set_manual_birthday(*, user_id: int, birthday: AnnualDate) -> None:
    async with transaction():
        await set_user_birthday_in_pg(
            user_id=user_id,
            birthday=birthday,
            source=UserBirthdaySource.manual,
        )


async def clear_birthday(user_id: int) -> None:
    async with transaction():
        await clear_user_birthday_in_pg(user_id)

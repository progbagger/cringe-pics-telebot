from dataclasses import dataclass
from enum import StrEnum

from .annual_date import AnnualDate


class UserBirthdaySource(StrEnum):
    telegram = "telegram"
    manual = "manual"


@dataclass(frozen=True, slots=True)
class UserBirthday:
    date: AnnualDate
    source: UserBirthdaySource

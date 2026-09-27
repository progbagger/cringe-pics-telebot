from dataclasses import dataclass
from datetime import datetime

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.user_birthday import UserBirthdaySource


@dataclass(slots=True)
class User:
    id: int
    """ID пользователя"""
    timezone_offset_minutes: int
    """Фиксированное смещение пользователя относительно UTC в минутах"""
    is_active: bool
    """Может ли бот отправлять пользователю административные рассылки"""
    created_at: datetime
    """Время создания пользователя"""
    birthday: AnnualDate | None = None
    """Сохранённые месяц и день рождения без года"""
    birthday_source: UserBirthdaySource | None = None
    """Источник сохранённой даты рождения"""

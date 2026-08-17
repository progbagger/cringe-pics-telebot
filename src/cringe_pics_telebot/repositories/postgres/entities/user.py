from dataclasses import dataclass
from datetime import datetime


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

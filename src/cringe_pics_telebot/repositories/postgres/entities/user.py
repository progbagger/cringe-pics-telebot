from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class User:
    id: int
    """ID пользователя"""
    timezone_offset_minutes: int
    """Фиксированное смещение пользователя относительно UTC в минутах"""
    created_at: datetime
    """Время создания пользователя"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class User:
    id: int
    """ID пользователя"""
    created_at: datetime
    """Время создания пользователя"""

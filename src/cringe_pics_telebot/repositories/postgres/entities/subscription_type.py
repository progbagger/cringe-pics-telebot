from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateSubscriptionType:
    name: str
    time: time | None
    s3_directory_path: str
    search_aliases: tuple[str, ...]


@dataclass(slots=True, eq=False)
class SubscriptionType:
    id: int
    """ID типа подписки"""
    name: str
    """Название типа подписки"""
    time: time | None
    """Локальное время отправки или ``None`` для категории без расписания"""
    s3_directory_path: str
    """Путь до папки с картинками в S3"""
    search_aliases: tuple[str, ...]
    """Дополнительные термины для inline-поиска"""
    is_active: bool
    """Доступен ли тип подписки пользователям"""
    created_at: datetime
    """Время создания типа подписки"""
    updated_at: datetime
    """Время обновления типа подписки"""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SubscriptionType):
            return NotImplemented

        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

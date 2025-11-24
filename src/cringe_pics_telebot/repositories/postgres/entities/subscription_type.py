from dataclasses import dataclass
from datetime import datetime, time


@dataclass(slots=True)
class SubscriptionType:
    id: int
    """ID типа подписки"""
    name: str
    """Название типа подписки"""
    time: time
    """Время, когда срабатывает подписка"""
    s3_directory_path: str
    """Путь до папки с картинками в S3"""
    created_at: datetime
    """Время создания типа подписки"""
    updated_at: datetime
    """Время обновления типа подписки"""

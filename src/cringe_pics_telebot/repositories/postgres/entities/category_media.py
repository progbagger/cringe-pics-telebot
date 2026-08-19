from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TelegramMediaType(StrEnum):
    photo = "photo"
    animation = "animation"


class CategoryMediaStatus(StrEnum):
    inactive = "inactive"
    pending = "pending"
    ready = "ready"


@dataclass(frozen=True, slots=True)
class CategoryMediaSource:
    source_path: str
    source_revision: str
    name: str
    mime_type: str
    telegram_media_type: TelegramMediaType


@dataclass(frozen=True, slots=True)
class CategoryMedia:
    id: int
    subscription_type_id: int
    source_path: str
    source_revision: str
    name: str
    mime_type: str
    telegram_media_type: TelegramMediaType
    telegram_file_id: str | None
    telegram_file_unique_id: str | None
    is_active: bool
    status: CategoryMediaStatus
    last_seen_at: datetime
    materialized_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CategoryMediaReconcileResult:
    discovered: int
    created: int
    changed: int
    reactivated: int
    deactivated: int
    unchanged: int

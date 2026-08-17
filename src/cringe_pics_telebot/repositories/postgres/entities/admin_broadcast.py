from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AdminBroadcastStatus(StrEnum):
    scheduled = "scheduled"
    sending = "sending"
    completed = "completed"
    deleted = "deleted"


@dataclass(slots=True, kw_only=True)
class AdminBroadcast:
    id: int
    created_by_user_id: int
    source_chat_id: int
    source_message_id: int
    scheduled_local_at: datetime
    timezone_offset_minutes: int | None
    status: AdminBroadcastStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deleted_at: datetime | None

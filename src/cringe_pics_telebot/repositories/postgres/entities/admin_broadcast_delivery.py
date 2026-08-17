from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AdminBroadcastDeliveryStatus(StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


@dataclass(slots=True, kw_only=True)
class AdminBroadcastDelivery:
    id: int
    broadcast_id: int
    user_id: int
    status: AdminBroadcastDeliveryStatus
    attempted_at: datetime
    finished_at: datetime | None
    error: str | None

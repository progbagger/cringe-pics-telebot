from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MediaAliasEnrichmentJobStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    retry = "retry"
    succeeded = "succeeded"
    failed = "failed"
    obsolete = "obsolete"


@dataclass(frozen=True, slots=True)
class MediaAliasEnrichmentJob:
    id: int
    media_id: int
    source_revision: str
    status: MediaAliasEnrichmentJobStatus
    attempt_count: int
    retry_count: int
    available_at: datetime
    lease_token: str | None
    leased_until: datetime | None
    model: str | None
    prompt_sha256: str | None
    result_class: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class MediaAliasEnrichmentQueueCounts:
    available: int
    expired_processing: int
    failed: int

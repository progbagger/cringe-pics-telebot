from datetime import time
from enum import StrEnum

DEFAULT_ANNUAL_SCHEDULE_TIME = time(12)


class SubscriptionScheduleKind(StrEnum):
    weekly = "weekly"
    annual_date = "annual_date"
    annual_birthday = "annual_birthday"

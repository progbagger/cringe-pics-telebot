from datetime import UTC, datetime, timedelta


def seconds_until_next_tick(*, current_time: datetime, interval: timedelta) -> float:
    interval_seconds = interval.total_seconds()
    current_second = current_time.second + current_time.microsecond / 1_000_000
    seconds_after_boundary = current_second % interval_seconds
    if seconds_after_boundary == 0:
        return interval_seconds
    return interval_seconds - seconds_after_boundary


def validate_interval(interval: timedelta) -> None:
    interval_seconds = interval.total_seconds()
    if interval_seconds <= 0 or interval_seconds > 60:
        raise ValueError("Scheduler interval must be between 0 and 60 seconds")


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

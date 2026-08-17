import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from cringe_pics_telebot.services.scheduler import aware_datetime
from cringe_pics_telebot.services.timezones import (
    MIN_TIMEZONE_OFFSET_MINUTES,
    InvalidTimezoneOffsetError,
    format_timezone_offset,
    parse_timezone_offset,
)

_SCHEDULE_PATTERN = re.compile(
    r"(?P<date>\d{2}\.\d{2}\.\d{4})\s+(?P<time>\d{2}:\d{2})(?:\s+(?P<offset>[+-]\d{2}:\d{2}))?"
)


class InvalidAdminBroadcastScheduleError(ValueError): ...


class PastAdminBroadcastScheduleError(InvalidAdminBroadcastScheduleError): ...


@dataclass(frozen=True, slots=True)
class AdminBroadcastSchedule:
    local_at: datetime
    timezone_offset_minutes: int | None


def parse_admin_broadcast_schedule(
    value: str,
    *,
    now: datetime | None = None,
) -> AdminBroadcastSchedule:
    match = _SCHEDULE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise InvalidAdminBroadcastScheduleError("Schedule must use DD.MM.YYYY HH:MM with an optional UTC offset")

    try:
        local_at = datetime.strptime(
            f"{match.group('date')} {match.group('time')}",
            "%d.%m.%Y %H:%M",
        )
    except ValueError as error:
        raise InvalidAdminBroadcastScheduleError("Schedule contains an invalid date or time") from error

    offset = match.group("offset")
    try:
        timezone_offset_minutes = parse_timezone_offset(offset) if offset is not None else None
    except InvalidTimezoneOffsetError as error:
        raise InvalidAdminBroadcastScheduleError(str(error)) from error

    comparison_offset = MIN_TIMEZONE_OFFSET_MINUTES if timezone_offset_minutes is None else timezone_offset_minutes
    current_local_time = (
        aware_datetime(now or datetime.now(UTC))
        .astimezone(timezone(timedelta(minutes=comparison_offset)))
        .replace(tzinfo=None)
    )
    if local_at <= current_local_time:
        raise PastAdminBroadcastScheduleError("Schedule has already passed")

    return AdminBroadcastSchedule(
        local_at=local_at,
        timezone_offset_minutes=timezone_offset_minutes,
    )


def format_admin_broadcast_schedule(
    local_at: datetime,
    timezone_offset_minutes: int | None,
    *,
    short: bool = False,
) -> str:
    date_format = "%d.%m %H:%M" if short else "%d.%m.%Y %H:%M"
    formatted = local_at.strftime(date_format)
    if timezone_offset_minutes is None:
        return f"{formatted} · локально" if short else f"{formatted} — локальное время каждого получателя"
    return f"{formatted} · UTC{format_timezone_offset(timezone_offset_minutes)}"


def format_admin_broadcast_countdown(
    local_at: datetime,
    timezone_offset_minutes: int | None,
    *,
    viewer_timezone_offset_minutes: int,
    now: datetime | None = None,
) -> str:
    effective_offset_minutes = (
        viewer_timezone_offset_minutes if timezone_offset_minutes is None else timezone_offset_minutes
    )
    scheduled_at = local_at.replace(
        tzinfo=timezone(timedelta(minutes=effective_offset_minutes)),
    )
    remaining_seconds = max(
        0,
        int((scheduled_at - aware_datetime(now or datetime.now(UTC))).total_seconds()),
    )

    if remaining_seconds >= 24 * 60 * 60:
        days, remainder = divmod(remaining_seconds, 24 * 60 * 60)
        hours, remainder = divmod(remainder, 60 * 60)
        minutes = remainder // 60
        return " ".join(
            (
                _format_duration_part(days, "день", "дня", "дней"),
                _format_duration_part(hours, "час", "часа", "часов"),
                _format_duration_part(minutes, "минута", "минуты", "минут"),
            )
        )

    if remaining_seconds >= 60 * 60:
        hours, remainder = divmod(remaining_seconds, 60 * 60)
        minutes = remainder // 60
        return " ".join(
            (
                _format_duration_part(hours, "час", "часа", "часов"),
                _format_duration_part(minutes, "минута", "минуты", "минут"),
            )
        )

    minutes, seconds = divmod(remaining_seconds, 60)
    return " ".join(
        (
            _format_duration_part(minutes, "минута", "минуты", "минут"),
            _format_duration_part(seconds, "секунда", "секунды", "секунд"),
        )
    )


def _format_duration_part(value: int, singular: str, paucal: str, plural: str) -> str:
    remainder = value % 100
    if 11 <= remainder <= 14:
        word = plural
    else:
        match value % 10:
            case 1:
                word = singular
            case 2 | 3 | 4:
                word = paucal
            case _:
                word = plural
    return f"{value} {word}"

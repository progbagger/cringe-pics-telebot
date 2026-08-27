from datetime import UTC, datetime

import pytest
from hamcrest import assert_that, equal_to, none

from cringe_pics_telebot.services.admin_broadcast_schedules import (
    InvalidAdminBroadcastScheduleError,
    PastAdminBroadcastScheduleError,
    format_admin_broadcast_countdown,
    format_admin_broadcast_schedule,
    parse_admin_broadcast_schedule,
)


def test_parse_local_recipient_schedule() -> None:
    schedule = parse_admin_broadcast_schedule(
        "20.08.2026 10:30",
        now=datetime(2026, 8, 19, 21, 0, tzinfo=UTC),
    )

    assert_that(schedule.local_at, equal_to(datetime(2026, 8, 20, 10, 30)))
    assert_that(schedule.timezone_offset_minutes, none())
    assert_that(
        format_admin_broadcast_schedule(schedule.local_at, schedule.timezone_offset_minutes),
        equal_to("20.08.2026 10:30 — локальное время каждого получателя"),
    )


def test_parse_fixed_timezone_override() -> None:
    schedule = parse_admin_broadcast_schedule(
        "20.08.2026 10:30 -05:30",
        now=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
    )

    assert_that(schedule.local_at, equal_to(datetime(2026, 8, 20, 10, 30)))
    assert_that(schedule.timezone_offset_minutes, equal_to(-330))
    assert_that(
        format_admin_broadcast_schedule(schedule.local_at, schedule.timezone_offset_minutes),
        equal_to("20.08.2026 10:30 · UTC-05:30"),
    )


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-20 10:30",
        "20.13.2026 10:30",
        "20.08.2026 25:30",
        "20.08.2026 10:30 +14:30",
    ],
)
def test_reject_invalid_schedule(value: str) -> None:
    with pytest.raises(InvalidAdminBroadcastScheduleError):
        parse_admin_broadcast_schedule(
            value,
            now=datetime(2026, 8, 17, tzinfo=UTC),
        )


def test_reject_schedule_that_passed_in_latest_timezone() -> None:
    with pytest.raises(PastAdminBroadcastScheduleError):
        parse_admin_broadcast_schedule(
            "17.08.2026 09:59",
            now=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("local_at", "timezone_offset_minutes", "viewer_timezone_offset_minutes", "now", "expected"),
    [
        (
            datetime(2026, 8, 19, 13, 23),
            240,
            -300,
            datetime(2026, 8, 17, 3, 0, tzinfo=UTC),
            "2 дня 6 часов 23 минуты",
        ),
        (
            datetime(2026, 8, 18, 3, 0),
            0,
            420,
            datetime(2026, 8, 17, 3, 0, tzinfo=UTC),
            "1 день 0 часов 0 минут",
        ),
        (
            datetime(2026, 8, 17, 6, 5),
            0,
            420,
            datetime(2026, 8, 17, 3, 0, tzinfo=UTC),
            "3 часа 5 минут",
        ),
        (
            datetime(2026, 8, 17, 4, 0),
            0,
            420,
            datetime(2026, 8, 17, 3, 0, tzinfo=UTC),
            "1 час 0 минут",
        ),
        (
            datetime(2026, 8, 17, 3, 59, 7),
            0,
            420,
            datetime(2026, 8, 17, 3, 0, tzinfo=UTC),
            "59 минут 7 секунд",
        ),
        (
            datetime(2026, 8, 17, 3, 0),
            0,
            420,
            datetime(2026, 8, 17, 3, 1, tzinfo=UTC),
            "0 минут 0 секунд",
        ),
        (
            datetime(2026, 8, 17, 10, 0),
            None,
            420,
            datetime(2026, 8, 17, 2, 30, tzinfo=UTC),
            "30 минут 0 секунд",
        ),
    ],
)
def test_format_admin_broadcast_countdown(
    local_at: datetime,
    timezone_offset_minutes: int | None,
    viewer_timezone_offset_minutes: int,
    now: datetime,
    expected: str,
) -> None:
    assert_that(
        format_admin_broadcast_countdown(
            local_at,
            timezone_offset_minutes,
            viewer_timezone_offset_minutes=viewer_timezone_offset_minutes,
            now=now,
        ),
        equal_to(expected),
    )

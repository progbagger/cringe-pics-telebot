from datetime import UTC, datetime

import pytest

from cringe_pics_telebot.services.admin_broadcast_schedules import (
    InvalidAdminBroadcastScheduleError,
    PastAdminBroadcastScheduleError,
    format_admin_broadcast_schedule,
    parse_admin_broadcast_schedule,
)


def test_parse_local_recipient_schedule() -> None:
    schedule = parse_admin_broadcast_schedule(
        "20.08.2026 10:30",
        now=datetime(2026, 8, 19, 21, 0, tzinfo=UTC),
    )

    assert schedule.local_at == datetime(2026, 8, 20, 10, 30)
    assert schedule.timezone_offset_minutes is None
    assert format_admin_broadcast_schedule(schedule.local_at, schedule.timezone_offset_minutes) == (
        "20.08.2026 10:30 — локальное время каждого получателя"
    )


def test_parse_fixed_timezone_override() -> None:
    schedule = parse_admin_broadcast_schedule(
        "20.08.2026 10:30 -05:30",
        now=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
    )

    assert schedule.local_at == datetime(2026, 8, 20, 10, 30)
    assert schedule.timezone_offset_minutes == -330
    assert format_admin_broadcast_schedule(schedule.local_at, schedule.timezone_offset_minutes) == (
        "20.08.2026 10:30 · UTC-05:30"
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

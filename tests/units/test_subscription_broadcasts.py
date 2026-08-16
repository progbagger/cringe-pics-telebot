from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from cringe_pics_telebot.services.subscription_broadcasts import _same_local_minute


@pytest.mark.parametrize(
    ("scheduled_time", "current_time", "timezone_offset_minutes", "expected"),
    [
        (time(10, 0), datetime(2026, 8, 16, 3, 0, tzinfo=UTC), 7 * 60, True),
        (time(10, 0), datetime(2026, 8, 16, 3, 0, tzinfo=UTC), 4 * 60, False),
        (time(10, 0), datetime(2026, 8, 16, 6, 0, tzinfo=UTC), 4 * 60, True),
        (time(23, 30), datetime(2026, 8, 17, 4, 30, tzinfo=UTC), -5 * 60, True),
        (time(1, 15), datetime(2026, 8, 16, 11, 15, tzinfo=UTC), 14 * 60, True),
        (
            time(10, 0, tzinfo=timezone(timedelta(hours=9))),
            datetime(2026, 8, 16, 3, 0, tzinfo=UTC),
            7 * 60,
            True,
        ),
        (time(10, 0), datetime(2026, 8, 16, 3, 1, tzinfo=UTC), 7 * 60, False),
    ],
)
def test_same_local_minute(
    scheduled_time: time,
    current_time: datetime,
    timezone_offset_minutes: int,
    expected: bool,
) -> None:
    assert (
        _same_local_minute(
            scheduled_time,
            current_time,
            timezone_offset_minutes=timezone_offset_minutes,
        )
        is expected
    )

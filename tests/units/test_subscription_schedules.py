from datetime import UTC, datetime, time

import pytest

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.subscription_schedule import (
    DEFAULT_ANNUAL_SCHEDULE_TIME,
    SubscriptionScheduleKind,
)
from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.services.subscription_schedules import (
    format_subscription_schedule,
    format_subscription_weekdays,
    is_subscription_due,
)


@pytest.mark.parametrize(
    ("weekdays", "daily_label", "expected"),
    [
        (SubscriptionWeekdays.daily(), "ежедневно", "ежедневно"),
        (SubscriptionWeekdays.daily(), "каждый день", "каждый день"),
        (SubscriptionWeekdays(1, 3, 5), "ежедневно", "Пн, Ср, Пт"),
        (SubscriptionWeekdays(6, 7), "ежедневно", "Сб, Вс"),
    ],
)
def test_format_subscription_weekdays(
    weekdays: SubscriptionWeekdays,
    daily_label: str,
    expected: str,
) -> None:
    assert format_subscription_weekdays(weekdays, daily_label=daily_label) == expected


@pytest.mark.parametrize(
    ("schedule_kind", "annual_date", "expected"),
    [
        (SubscriptionScheduleKind.weekly, None, "Пн, Ср, Пт"),
        (SubscriptionScheduleKind.annual_date, AnnualDate(month=1, day=1), "ежегодно 01.01"),
        (SubscriptionScheduleKind.annual_birthday, None, "ежегодно в твой день рождения"),
    ],
)
def test_format_subscription_schedule(
    schedule_kind: SubscriptionScheduleKind,
    annual_date: AnnualDate | None,
    expected: str,
) -> None:
    assert (
        format_subscription_schedule(
            schedule_kind=schedule_kind,
            weekdays=SubscriptionWeekdays(1, 3, 5),
            annual_date=annual_date,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("month", "day"),
    [(0, 1), (13, 1), (2, 30), (4, 31)],
)
def test_annual_date_rejects_impossible_values(month: int, day: int) -> None:
    with pytest.raises(ValueError):
        AnnualDate(month=month, day=day)


def test_annual_date_accepts_february_29() -> None:
    assert AnnualDate(month=2, day=29).format() == "29.02"


def test_annual_schedules_default_to_local_noon() -> None:
    assert time(12) == DEFAULT_ANNUAL_SCHEDULE_TIME


@pytest.mark.parametrize(
    (
        "schedule_kind",
        "weekdays",
        "annual_date",
        "birthday",
        "current_time",
        "timezone_offset_minutes",
        "expected",
    ),
    [
        (
            SubscriptionScheduleKind.weekly,
            SubscriptionWeekdays(1),
            None,
            None,
            datetime(2026, 12, 28, 3, tzinfo=UTC),
            7 * 60,
            True,
        ),
        (
            SubscriptionScheduleKind.weekly,
            SubscriptionWeekdays(2),
            None,
            None,
            datetime(2026, 12, 28, 3, tzinfo=UTC),
            7 * 60,
            False,
        ),
        (
            SubscriptionScheduleKind.annual_date,
            SubscriptionWeekdays.daily(),
            AnnualDate(month=1, day=1),
            None,
            datetime(2026, 12, 31, 22, tzinfo=UTC),
            12 * 60,
            True,
        ),
        (
            SubscriptionScheduleKind.annual_date,
            SubscriptionWeekdays.daily(),
            AnnualDate(month=1, day=1),
            None,
            datetime(2026, 12, 31, 22, tzinfo=UTC),
            -7 * 60,
            False,
        ),
        (
            SubscriptionScheduleKind.annual_birthday,
            SubscriptionWeekdays.daily(),
            None,
            AnnualDate(month=9, day=26),
            datetime(2026, 9, 26, 3, tzinfo=UTC),
            7 * 60,
            True,
        ),
        (
            SubscriptionScheduleKind.annual_birthday,
            SubscriptionWeekdays.daily(),
            None,
            None,
            datetime(2026, 9, 26, 3, tzinfo=UTC),
            7 * 60,
            False,
        ),
        (
            SubscriptionScheduleKind.annual_date,
            SubscriptionWeekdays.daily(),
            AnnualDate(month=2, day=29),
            None,
            datetime(2028, 2, 29, 3, tzinfo=UTC),
            7 * 60,
            True,
        ),
        (
            SubscriptionScheduleKind.annual_date,
            SubscriptionWeekdays.daily(),
            AnnualDate(month=2, day=29),
            None,
            datetime(2027, 2, 28, 3, tzinfo=UTC),
            7 * 60,
            False,
        ),
    ],
)
def test_is_subscription_due_uses_local_schedule_policy(
    schedule_kind: SubscriptionScheduleKind,
    weekdays: SubscriptionWeekdays,
    annual_date: AnnualDate | None,
    birthday: AnnualDate | None,
    current_time: datetime,
    timezone_offset_minutes: int,
    expected: bool,
) -> None:
    assert (
        is_subscription_due(
            scheduled_time=time(10),
            schedule_kind=schedule_kind,
            weekdays=weekdays,
            annual_date=annual_date,
            birthday=birthday,
            current_time=current_time,
            timezone_offset_minutes=timezone_offset_minutes,
        )
        is expected
    )


def test_is_subscription_due_requires_exact_local_minute() -> None:
    assert not is_subscription_due(
        scheduled_time=time(10),
        schedule_kind=SubscriptionScheduleKind.annual_date,
        weekdays=SubscriptionWeekdays.daily(),
        annual_date=AnnualDate(month=1, day=1),
        birthday=None,
        current_time=datetime(2026, 1, 1, 3, 1, tzinfo=UTC),
        timezone_offset_minutes=7 * 60,
    )

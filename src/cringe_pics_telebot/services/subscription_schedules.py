from datetime import datetime, time, timedelta, timezone

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.subscription_schedule import SubscriptionScheduleKind
from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.services.scheduler import aware_datetime

_WEEKDAY_ABBREVIATIONS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def format_subscription_weekdays(
    weekdays: SubscriptionWeekdays,
    *,
    daily_label: str = "ежедневно",
) -> str:
    if len(weekdays) == len(_WEEKDAY_ABBREVIATIONS):
        return daily_label
    return ", ".join(_WEEKDAY_ABBREVIATIONS[day - 1] for day in weekdays)


def format_subscription_schedule(
    *,
    schedule_kind: SubscriptionScheduleKind,
    weekdays: SubscriptionWeekdays,
    annual_date: AnnualDate | None,
    daily_label: str = "ежедневно",
) -> str:
    match schedule_kind:
        case SubscriptionScheduleKind.weekly:
            return format_subscription_weekdays(weekdays, daily_label=daily_label)
        case SubscriptionScheduleKind.annual_date:
            if annual_date is None:
                raise ValueError("Fixed annual schedule requires an annual date")
            return f"ежегодно {annual_date.format()}"
        case SubscriptionScheduleKind.annual_birthday:
            return "ежегодно в твой день рождения"


def is_subscription_due(
    *,
    scheduled_time: time,
    schedule_kind: SubscriptionScheduleKind,
    weekdays: SubscriptionWeekdays,
    annual_date: AnnualDate | None,
    birthday: AnnualDate | None,
    current_time: datetime,
    timezone_offset_minutes: int,
) -> bool:
    local_datetime = aware_datetime(current_time).astimezone(timezone(timedelta(minutes=timezone_offset_minutes)))
    if scheduled_time.hour != local_datetime.hour or scheduled_time.minute != local_datetime.minute:
        return False

    match schedule_kind:
        case SubscriptionScheduleKind.weekly:
            return local_datetime.isoweekday() in weekdays
        case SubscriptionScheduleKind.annual_date:
            return _matches_annual_date(local_datetime, annual_date)
        case SubscriptionScheduleKind.annual_birthday:
            return _matches_annual_date(local_datetime, birthday)


def _matches_annual_date(local_datetime: datetime, annual_date: AnnualDate | None) -> bool:
    return (
        annual_date is not None and local_datetime.month == annual_date.month and local_datetime.day == annual_date.day
    )

import pytest

from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.services.subscription_schedules import format_subscription_weekdays


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

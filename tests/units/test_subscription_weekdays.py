import pytest
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays


def test_subscription_weekdays_daily_contains_all_iso_days() -> None:
    weekdays = SubscriptionWeekdays.daily()

    assert_that(weekdays.days, equal_to((1, 2, 3, 4, 5, 6, 7)))
    assert_that(weekdays.mask, equal_to(127))


def test_subscription_weekdays_normalizes_calendar_order_and_round_trips_mask() -> None:
    weekdays = SubscriptionWeekdays(5, 1, 3)

    assert_that(weekdays.days, equal_to((1, 3, 5)))
    assert_that(weekdays.mask, equal_to(21))
    assert_that(SubscriptionWeekdays.from_mask(weekdays.mask), equal_to(weekdays))


@pytest.mark.parametrize(
    "days",
    [(), (0,), (8,), (1, 1)],
)
def test_subscription_weekdays_rejects_empty_invalid_or_duplicate_days(days: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        SubscriptionWeekdays(*days)


@pytest.mark.parametrize("mask", [0, 128, -1])
def test_subscription_weekdays_rejects_invalid_mask(mask: int) -> None:
    with pytest.raises(ValueError):
        SubscriptionWeekdays.from_mask(mask)

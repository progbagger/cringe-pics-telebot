from datetime import time

import pytest
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.bot.inline_pagination import paginate_inline_keyboard
from cringe_pics_telebot.bot.keyboards import create_inline_subscriptions_keyboard
from cringe_pics_telebot.bot.subscription_callback_data import (
    SubscriptionActionCallbackData,
    SubscriptionCallbackData,
    SubscriptionPageCallbackData,
)
from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo


@pytest.mark.parametrize(
    ("item_count", "requested_page", "expected_items", "previous_page", "next_page"),
    [
        (0, 0, (), None, None),
        (1, 0, (0,), None, None),
        (8, 0, tuple(range(8)), None, None),
        (9, 0, tuple(range(8)), None, 1),
        (17, 1, tuple(range(8, 16)), 0, 2),
        (17, 2, (16,), 1, None),
        (9, -1, tuple(range(8)), None, 1),
        (9, 100, (8,), 0, None),
    ],
)
def test_paginate_inline_keyboard_normalizes_page_and_exposes_available_directions(
    item_count: int,
    requested_page: int,
    expected_items: tuple[int, ...],
    previous_page: int | None,
    next_page: int | None,
) -> None:
    page = paginate_inline_keyboard(tuple(range(item_count)), requested_page)

    assert_that(page.items, equal_to(expected_items))
    assert_that(page.previous_number, equal_to(previous_page))
    assert_that(page.next_number, equal_to(next_page))


@pytest.mark.parametrize(
    ("item_count", "requested_page", "expected_items"),
    [
        (4, 0, (0, 1, 2, 3)),
        (5, 0, (0, 1, 2, 3)),
        (5, 1, (4,)),
        (9, 2, (8,)),
    ],
)
def test_paginate_inline_keyboard_keeps_two_row_items_together(
    item_count: int,
    requested_page: int,
    expected_items: tuple[int, ...],
) -> None:
    page = paginate_inline_keyboard(tuple(range(item_count)), requested_page, rows_per_item=2)

    assert_that(page.items, equal_to(expected_items))


@pytest.mark.parametrize("rows_per_item", [0, 9])
def test_paginate_inline_keyboard_rejects_invalid_row_count(rows_per_item: int) -> None:
    with pytest.raises(ValueError, match="rows_per_item"):
        paginate_inline_keyboard((), 0, rows_per_item=rows_per_item)


@pytest.mark.parametrize(
    ("item_count", "page", "expected_navigation"),
    [
        (0, 0, []),
        (8, 0, []),
        (9, 0, [">"]),
        (17, 1, ["<", ">"]),
        (17, 2, ["<"]),
    ],
)
def test_subscription_keyboard_shows_only_available_navigation(
    item_count: int,
    page: int,
    expected_navigation: list[str],
) -> None:
    keyboard = create_inline_subscriptions_keyboard(_subscriptions(item_count), page=page)
    navigation = [button.text for row in keyboard.inline_keyboard for button in row if button.text in {"<", ">"}]

    assert_that(navigation, equal_to(expected_navigation))


def test_subscription_callbacks_fit_telegram_limit_and_legacy_callback_still_unpacks() -> None:
    callback_data = SubscriptionActionCallbackData(
        category_id=9_223_372_036_854_775_807,
        subscribe=True,
        page=9_223_372_036_854_775_807,
    ).pack()

    assert len(callback_data.encode()) <= 64
    assert_that(
        SubscriptionCallbackData.unpack("subscription:42:1"),
        equal_to(SubscriptionCallbackData(category_id=42, subscribe=True)),
    )
    assert_that(SubscriptionPageCallbackData.unpack("subscription_page:3").page, equal_to(3))


def _subscriptions(count: int) -> list[SubscriptionInfo]:
    return [
        SubscriptionInfo(
            id=index + 1,
            name=f"/category-{index:02d}",
            send_time=time(index % 24),
            weekdays=SubscriptionWeekdays.daily(),
            subscribed=False,
        )
        for index in range(count)
    ]

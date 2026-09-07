from datetime import UTC, datetime, time

import pytest
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.bot.admin_broadcast_callback_data import (
    AdminBroadcastAction,
    AdminBroadcastPagedCallbackData,
)
from cringe_pics_telebot.bot.admin_category_callback_data import (
    AdminCategoryAction,
    AdminCategoryPagedCallbackData,
)
from cringe_pics_telebot.bot.admin_keyboards import (
    create_admin_broadcasts_keyboard,
    create_admin_categories_keyboard,
)
from cringe_pics_telebot.bot.inline_pagination import paginate_inline_keyboard
from cringe_pics_telebot.bot.keyboards import create_inline_subscriptions_keyboard
from cringe_pics_telebot.bot.subscription_callback_data import (
    SubscriptionActionCallbackData,
    SubscriptionCallbackData,
    SubscriptionPageCallbackData,
)
from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.repositories.postgres.entities import (
    AdminBroadcast,
    AdminBroadcastStatus,
    SubscriptionType,
)


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


def test_admin_category_keyboard_sorts_before_paging_and_places_constants_before_navigation() -> None:
    categories = _categories(9)
    categories.reverse()

    keyboard = create_admin_categories_keyboard(categories)
    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]

    assert_that(
        rows,
        equal_to(
            [
                *[[f"✅ /category-{index:02d} — активна"] for index in range(8)],
                ["Создать категорию"],
                ["Назад"],
                [">"],
            ]
        ),
    )


def test_admin_broadcast_keyboard_counts_two_dynamic_rows_per_item() -> None:
    broadcasts = _broadcasts(5)

    first_page = create_admin_broadcasts_keyboard(broadcasts)
    last_page = create_admin_broadcasts_keyboard(broadcasts, page=1)

    assert_that([row[0].text for row in first_page.inline_keyboard[:8:2]], equal_to(_broadcast_labels(4)))
    assert_that(
        [[button.text for button in row] for row in first_page.inline_keyboard[-3:]],
        equal_to([["Новое уведомление"], ["Назад"], [">"]]),
    )
    assert_that(last_page.inline_keyboard[0][0].text, equal_to(_broadcast_labels(5)[-1]))
    assert_that(
        [[button.text for button in row] for row in last_page.inline_keyboard[-3:]],
        equal_to([["Новое уведомление"], ["Назад"], ["<"]]),
    )


def test_admin_paged_callbacks_fit_telegram_limit() -> None:
    category_callback = AdminCategoryPagedCallbackData(
        action=AdminCategoryAction.disable_schedule,
        category_id=9_223_372_036_854_775_807,
        page=2_147_483_647,
    ).pack()
    broadcast_callback = AdminBroadcastPagedCallbackData(
        action=AdminBroadcastAction.confirm_delete,
        broadcast_id=9_223_372_036_854_775_807,
        page=2_147_483_647,
    ).pack()

    assert len(category_callback.encode()) <= 64
    assert len(broadcast_callback.encode()) <= 64


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


def _categories(count: int) -> list[SubscriptionType]:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    return [
        SubscriptionType(
            id=index + 1,
            name=f"/category-{index:02d}",
            time=time(index),
            s3_directory_path=f"category-{index:02d}",
            search_aliases=(),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        for index in range(count)
    ]


def _broadcasts(count: int) -> list[AdminBroadcast]:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    return [
        AdminBroadcast(
            id=index + 1,
            created_by_user_id=42,
            source_chat_id=42,
            source_message_id=index + 1,
            scheduled_local_at=datetime(2026, 9, 7 + index, 10),
            timezone_offset_minutes=420,
            status=AdminBroadcastStatus.scheduled,
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
            deleted_at=None,
        )
        for index in range(count)
    ]


def _broadcast_labels(count: int) -> list[str]:
    return [f"{7 + index:02d}.09 10:00 · UTC+07:00" for index in range(count)]

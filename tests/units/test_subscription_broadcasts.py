from datetime import UTC, datetime, time, timedelta, timezone
from typing import cast

import pytest
from aiogram import Bot
from hamcrest import assert_that, equal_to, is_
from pytest import MonkeyPatch

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaStatus,
    SubscriptionType,
    TelegramMediaType,
    User,
)
from cringe_pics_telebot.services import subscription_broadcasts
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
    assert_that(
        _same_local_minute(
            scheduled_time,
            current_time,
            timezone_offset_minutes=timezone_offset_minutes,
        ),
        is_(expected),
    )


async def test_broadcast_fetches_media_once_and_prefers_pending_for_every_recipient(
    monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)
    subscription_type = _subscription_type()
    users = [_user(701), _user(702)]
    ready = _media(1, status=CategoryMediaStatus.ready)
    pending = _media(2, status=CategoryMediaStatus.pending)
    requested_category_ids: list[list[int]] = []
    delivered: list[CategoryMedia] = []

    async def get_users(subscription_type_id: int) -> list[User]:
        assert_that(subscription_type_id, equal_to(subscription_type.id))
        return users

    async def get_media(category_ids: list[int]) -> list[CategoryMedia]:
        requested_category_ids.append(category_ids)
        return [ready, pending]

    async def reserve(**kwargs: object) -> bool:
        return True

    async def deliver(media: CategoryMedia, **kwargs: object) -> None:
        delivered.append(media)

    monkeypatch.setattr(subscription_broadcasts, "get_subscription_users", get_users)
    monkeypatch.setattr(subscription_broadcasts, "get_category_media_by_subscription_types", get_media)
    monkeypatch.setattr(subscription_broadcasts, "_reserve_scheduled_send", reserve)
    monkeypatch.setattr(subscription_broadcasts, "deliver_category_media", deliver)

    sent = await subscription_broadcasts._broadcast_subscription_type(
        bot=cast(Bot, object()),
        subscription_type=subscription_type,
        current_time=now,
    )

    assert_that(sent, equal_to(2))
    assert_that(requested_category_ids, equal_to([[subscription_type.id]]))
    assert_that(delivered, equal_to([pending, pending]))


async def test_broadcast_does_not_reserve_empty_category(monkeypatch: MonkeyPatch) -> None:
    reservation_attempted = False

    async def get_users(subscription_type_id: int) -> list[User]:
        return [_user(701)]

    async def get_media(category_ids: list[int]) -> list[CategoryMedia]:
        return []

    async def reserve(**kwargs: object) -> bool:
        nonlocal reservation_attempted
        reservation_attempted = True
        return True

    monkeypatch.setattr(subscription_broadcasts, "get_subscription_users", get_users)
    monkeypatch.setattr(subscription_broadcasts, "get_category_media_by_subscription_types", get_media)
    monkeypatch.setattr(subscription_broadcasts, "_reserve_scheduled_send", reserve)

    sent = await subscription_broadcasts._broadcast_subscription_type(
        bot=cast(Bot, object()),
        subscription_type=_subscription_type(),
        current_time=datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
    )

    assert_that(sent, equal_to(0))
    assert_that(reservation_attempted, is_(False))


def _subscription_type() -> SubscriptionType:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    return SubscriptionType(
        id=1,
        name="/morning",
        time=time(10, 0),
        s3_directory_path="morning",
        search_aliases=(),
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _user(user_id: int) -> User:
    return User(
        id=user_id,
        timezone_offset_minutes=7 * 60,
        is_active=True,
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
    )


def _media(media_id: int, *, status: CategoryMediaStatus) -> CategoryMedia:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    ready = status is CategoryMediaStatus.ready
    return CategoryMedia(
        id=media_id,
        subscription_type_id=1,
        source_path=f"morning/{media_id}.png",
        source_revision=f"sha256:{media_id}",
        name=f"{media_id}.png",
        mime_type="image/png",
        telegram_media_type=TelegramMediaType.photo,
        telegram_file_id=f"telegram-{media_id}" if ready else None,
        telegram_file_unique_id=f"unique-{media_id}" if ready else None,
        is_active=True,
        status=status,
        last_seen_at=now,
        materialized_at=now if ready else None,
        created_at=now,
        updated_at=now,
    )

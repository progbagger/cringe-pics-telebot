from datetime import UTC, datetime

from hamcrest import assert_that, is_

from cringe_pics_telebot.repositories.postgres.entities import (
    AdminBroadcast,
    AdminBroadcastStatus,
    User,
)
from cringe_pics_telebot.services.admin_broadcasts import (
    is_admin_broadcast_complete,
    is_admin_broadcast_due,
)


def test_broadcast_without_override_uses_each_users_timezone() -> None:
    broadcast = _broadcast(scheduled_local_at=datetime(2026, 8, 17, 10, 0))
    current_time = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)

    assert_that(is_admin_broadcast_due(broadcast, user=_user(700, 420), now=current_time), is_(True))
    assert_that(is_admin_broadcast_due(broadcast, user=_user(400, 240), now=current_time), is_(False))


def test_broadcast_override_uses_one_fixed_timezone_for_all_users() -> None:
    broadcast = _broadcast(
        scheduled_local_at=datetime(2026, 8, 17, 10, 0),
        timezone_offset_minutes=420,
    )
    current_time = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)

    assert_that(is_admin_broadcast_due(broadcast, user=_user(700, 420), now=current_time), is_(True))
    assert_that(is_admin_broadcast_due(broadcast, user=_user(400, 240), now=current_time), is_(True))
    assert_that(is_admin_broadcast_due(broadcast, user=_user(-500, -300), now=current_time), is_(True))


def test_local_broadcast_completes_only_after_latest_supported_timezone() -> None:
    broadcast = _broadcast(scheduled_local_at=datetime(2026, 8, 17, 10, 0))

    assert_that(
        is_admin_broadcast_complete(
            broadcast,
            now=datetime(2026, 8, 17, 21, 59, tzinfo=UTC),
        ),
        is_(False),
    )
    assert_that(
        is_admin_broadcast_complete(
            broadcast,
            now=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
        ),
        is_(True),
    )


def _broadcast(*, scheduled_local_at: datetime, timezone_offset_minutes: int | None = None) -> AdminBroadcast:
    created_at = datetime(2026, 8, 17, tzinfo=UTC)
    return AdminBroadcast(
        id=1,
        created_by_user_id=42,
        source_chat_id=42,
        source_message_id=100,
        scheduled_local_at=scheduled_local_at,
        timezone_offset_minutes=timezone_offset_minutes,
        status=AdminBroadcastStatus.scheduled,
        created_at=created_at,
        updated_at=created_at,
        started_at=None,
        completed_at=None,
        deleted_at=None,
    )


def _user(user_id: int, timezone_offset_minutes: int) -> User:
    return User(
        id=user_id,
        timezone_offset_minutes=timezone_offset_minutes,
        is_active=True,
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
    )

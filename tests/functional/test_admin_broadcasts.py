from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.functional.conftest import FakeTelegramServer, FunctionalSubscriptionType


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state(())


async def test_local_broadcast_sends_to_all_active_users_in_their_timezones_once(
    fake_telegram_server: FakeTelegramServer,
    create_functional_user: Callable[..., Awaitable[None]],
    create_functional_admin_broadcast: Callable[..., Awaitable[int]],
    read_admin_broadcast_state: Callable[[int], Awaitable[tuple[str, list[tuple[int, str]]]]],
    run_admin_broadcasts_at: Callable[[datetime], Awaitable[int]],
) -> None:
    await create_functional_user(user_id=700, timezone_offset_minutes=420)
    await create_functional_user(user_id=400, timezone_offset_minutes=240)
    broadcast_id = await create_functional_admin_broadcast(
        scheduled_local_at=datetime(2026, 8, 17, 10, 0),
    )

    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 3, 0, tzinfo=UTC)) == 1
    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 3, 0, 30, tzinfo=UTC)) == 0
    assert _copied_chat_ids(await fake_telegram_server.requests(method="copyMessage")) == [700]
    assert await read_admin_broadcast_state(broadcast_id) == ("sending", [(700, "sent")])

    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 6, 0, tzinfo=UTC)) == 1
    assert _copied_chat_ids(await fake_telegram_server.requests(method="copyMessage")) == [400, 700]
    assert await read_admin_broadcast_state(broadcast_id) == (
        "sending",
        [(400, "sent"), (700, "sent")],
    )

    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 22, 0, tzinfo=UTC)) == 0
    assert await read_admin_broadcast_state(broadcast_id) == (
        "completed",
        [(400, "sent"), (700, "sent")],
    )


async def test_timezone_override_sends_to_every_user_at_one_instant(
    fake_telegram_server: FakeTelegramServer,
    create_functional_user: Callable[..., Awaitable[None]],
    create_functional_admin_broadcast: Callable[..., Awaitable[int]],
    read_admin_broadcast_state: Callable[[int], Awaitable[tuple[str, list[tuple[int, str]]]]],
    run_admin_broadcasts_at: Callable[[datetime], Awaitable[int]],
) -> None:
    await create_functional_user(user_id=700, timezone_offset_minutes=420)
    await create_functional_user(user_id=400, timezone_offset_minutes=240)
    broadcast_id = await create_functional_admin_broadcast(
        scheduled_local_at=datetime(2026, 8, 17, 10, 0),
        timezone_offset_minutes=420,
    )

    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 2, 59, tzinfo=UTC)) == 0
    assert not await fake_telegram_server.requests(method="copyMessage")

    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 3, 0, tzinfo=UTC)) == 2
    assert _copied_chat_ids(await fake_telegram_server.requests(method="copyMessage")) == [400, 700]
    assert await read_admin_broadcast_state(broadcast_id) == (
        "completed",
        [(400, "sent"), (700, "sent")],
    )


async def test_forbidden_user_is_deactivated_without_losing_profile(
    fake_telegram_server: FakeTelegramServer,
    create_functional_user: Callable[..., Awaitable[None]],
    create_functional_admin_broadcast: Callable[..., Awaitable[int]],
    read_admin_broadcast_state: Callable[[int], Awaitable[tuple[str, list[tuple[int, str]]]]],
    read_user_state: Callable[[int], Awaitable[tuple[int, bool] | None]],
    run_admin_broadcasts_at: Callable[[datetime], Awaitable[int]],
) -> None:
    await create_functional_user(user_id=700, timezone_offset_minutes=-300)
    await create_functional_user(user_id=400, timezone_offset_minutes=240)
    broadcast_id = await create_functional_admin_broadcast(
        scheduled_local_at=datetime(2026, 8, 17, 10, 0),
        timezone_offset_minutes=420,
    )
    await fake_telegram_server.set_forbidden_chat_ids(700)

    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 3, 0, tzinfo=UTC)) == 1

    assert await read_user_state(700) == (-300, False)
    assert await read_user_state(400) == (240, True)
    assert await read_admin_broadcast_state(broadcast_id) == (
        "completed",
        [(400, "sent"), (700, "failed")],
    )


async def test_explicit_inactive_recipient_is_added_to_one_broadcast(
    fake_telegram_server: FakeTelegramServer,
    create_functional_user: Callable[..., Awaitable[None]],
    create_functional_admin_broadcast: Callable[..., Awaitable[int]],
    set_functional_admin_broadcast_recipients: Callable[..., Awaitable[None]],
    read_user_state: Callable[[int], Awaitable[tuple[int, bool] | None]],
    run_admin_broadcasts_at: Callable[[datetime], Awaitable[int]],
) -> None:
    await create_functional_user(user_id=400, timezone_offset_minutes=240)
    broadcast_id = await create_functional_admin_broadcast(
        scheduled_local_at=datetime(2026, 8, 17, 10, 0),
        timezone_offset_minutes=420,
    )
    await set_functional_admin_broadcast_recipients(
        broadcast_id=broadcast_id,
        user_ids=(900,),
    )

    assert await read_user_state(900) == (420, False)
    assert await run_admin_broadcasts_at(datetime(2026, 8, 17, 3, 0, tzinfo=UTC)) == 2
    assert _copied_chat_ids(await fake_telegram_server.requests(method="copyMessage")) == [400, 900]
    assert await read_user_state(900) == (420, False)


def _copied_chat_ids(requests: list[dict[str, Any]]) -> list[int]:
    return sorted(int(request["payload"]["chat_id"]) for request in requests)

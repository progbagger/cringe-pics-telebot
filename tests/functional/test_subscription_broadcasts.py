from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time
from typing import Any

import pytest

from cringe_pics_telebot.services.media_sync import MediaSyncSummary
from tests.functional.conftest import (
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state(())


async def test_subscription_broadcasts_use_each_users_local_time_without_duplicates(
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/morning", time(10, 0), "morning"),))
    await create_user_subscription(
        user_id=700,
        subscription_type_id=1,
        timezone_offset_minutes=7 * 60,
    )
    await create_user_subscription(
        user_id=400,
        subscription_type_id=1,
        timezone_offset_minutes=4 * 60,
    )
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    assert await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)) == 1
    assert await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, 45, tzinfo=UTC)) == 0
    assert _sent_chat_ids(await fake_telegram_server.requests(method="sendPhoto")) == [700]

    assert await run_subscription_broadcasts_at(datetime(2026, 8, 16, 6, 0, tzinfo=UTC)) == 1
    send_photo_requests = await fake_telegram_server.requests(method="sendPhoto")
    assert _sent_chat_ids(send_photo_requests) == [700, 400]
    assert [request["payload"]["photo"] for request in send_photo_requests] == [
        f"{fake_yandex_server.base_url}/download/image.png",
        "functional-photo-file-id",
    ]

    yandex_requests = await fake_yandex_server.requests()
    assert not any(request["method"] == "resources" for request in yandex_requests)
    assert any(request["method"] == "resources/download" for request in yandex_requests)
    assert not any(request["method"] == "download" for request in yandex_requests)


async def test_subscription_broadcasts_skip_empty_category(
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/empty", time(10, 0), "empty"),))
    await create_user_subscription(
        user_id=700,
        subscription_type_id=1,
        timezone_offset_minutes=7 * 60,
    )
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    assert await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)) == 0
    assert not await fake_telegram_server.requests(method="sendPhoto")
    assert not await fake_yandex_server.requests()


async def test_subscription_broadcast_prefers_pending_media_over_ready(
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
    read_functional_category_media_states: Callable[[], Awaitable[dict[str, tuple[str, str | None]]]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/morning", time(10, 0), "morning"),))
    await create_user_subscription(
        user_id=700,
        subscription_type_id=1,
        timezone_offset_minutes=7 * 60,
    )
    await fake_yandex_server.configure_directory(
        "morning",
        images=[{"name": "ready.png"}, {"name": "pending.png"}],
    )
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids({"morning/ready.png": "functional-ready-file-id"})
    await fake_telegram_server.reset()
    await fake_yandex_server.reset()

    assert await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)) == 1

    send_photo_requests = await fake_telegram_server.requests(method="sendPhoto")
    assert [request["payload"]["photo"] for request in send_photo_requests] == [
        f"{fake_yandex_server.base_url}/download/pending.png"
    ]
    assert sum(request["method"] == "resources/download" for request in await fake_yandex_server.requests()) == 1
    assert await read_functional_category_media_states() == {
        "morning/pending.png": ("ready", "functional-photo-file-id"),
        "morning/ready.png": ("ready", "functional-ready-file-id"),
    }


async def test_concurrent_scheduled_recipients_materialize_one_pending_revision_once(
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/morning", time(10, 0), "morning"),))
    for user_id in (701, 702):
        await create_user_subscription(
            user_id=user_id,
            subscription_type_id=1,
            timezone_offset_minutes=7 * 60,
        )
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    assert await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)) == 2

    send_photo_requests = await fake_telegram_server.requests(method="sendPhoto")
    assert sorted(_sent_chat_ids(send_photo_requests)) == [701, 702]
    sent_media = [request["payload"]["photo"] for request in send_photo_requests]
    assert sent_media.count(f"{fake_yandex_server.base_url}/download/image.png") == 1
    assert sent_media.count("functional-photo-file-id") == 1
    assert sum(request["method"] == "resources/download" for request in await fake_yandex_server.requests()) == 1


def _sent_chat_ids(requests: list[dict[str, Any]]) -> list[int]:
    return [int(request["payload"]["chat_id"]) for request in requests]

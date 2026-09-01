import asyncio
from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time
from typing import Any

import pytest
from hamcrest import assert_that, empty, equal_to, has_item

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

    assert_that(await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)), equal_to(1))
    assert_that(await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, 45, tzinfo=UTC)), equal_to(0))
    assert_that(_sent_chat_ids(await fake_telegram_server.requests(method="sendPhoto")), equal_to([700]))

    assert_that(await run_subscription_broadcasts_at(datetime(2026, 8, 16, 6, 0, tzinfo=UTC)), equal_to(1))
    send_photo_requests = await fake_telegram_server.requests(method="sendPhoto")
    assert_that(_sent_chat_ids(send_photo_requests), equal_to([700, 400]))
    assert_that(
        [request["payload"]["photo"] for request in send_photo_requests],
        equal_to(
            [
                f"{fake_yandex_server.base_url}/download/image.png",
                "functional-photo-file-id",
            ]
        ),
    )

    yandex_methods = [request["method"] for request in await fake_yandex_server.requests()]
    assert_that([method for method in yandex_methods if method == "resources"], empty())
    assert_that(yandex_methods, has_item("resources/download"))
    assert_that([method for method in yandex_methods if method == "download"], empty())


async def test_inactive_subscription_broadcast_resumes_after_reactivation_without_deleting_subscription(
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    count_user_subscriptions: Callable[[int], Awaitable[int]],
    set_functional_subscription_type_activity: Callable[[int, bool], Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await seed_functional_subscription_types(
        (FunctionalSubscriptionType(1, "/morning", time(10), "morning", is_active=False),)
    )
    await create_user_subscription(
        user_id=700,
        subscription_type_id=1,
        timezone_offset_minutes=7 * 60,
    )
    sync = await synchronize_functional_media_catalog()
    assert_that(sync.categories, equal_to(1))
    await fake_yandex_server.reset()

    due_at = datetime(2026, 8, 16, 3, 0, tzinfo=UTC)
    assert_that(await run_subscription_broadcasts_at(due_at), equal_to(0))
    assert_that(await count_user_subscriptions(700), equal_to(1))
    assert_that(await fake_telegram_server.requests(method="sendPhoto"), empty())

    await set_functional_subscription_type_activity(1, True)
    assert_that(await run_subscription_broadcasts_at(due_at), equal_to(1))
    assert_that(await count_user_subscriptions(700), equal_to(1))
    assert_that(_sent_chat_ids(await fake_telegram_server.requests(method="sendPhoto")), equal_to([700]))


async def test_subscription_broadcasts_skip_category_without_schedule(
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await seed_functional_subscription_types(
        (
            FunctionalSubscriptionType(1, "/instant", None, "instant"),
            FunctionalSubscriptionType(2, "/scheduled", time(10), "scheduled"),
        )
    )
    await create_user_subscription(user_id=701, subscription_type_id=1, timezone_offset_minutes=7 * 60)
    await create_user_subscription(user_id=700, subscription_type_id=2, timezone_offset_minutes=7 * 60)
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    sent_count = await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC))

    assert_that(sent_count, equal_to(1))
    assert_that(_sent_chat_ids(await fake_telegram_server.requests(method="sendPhoto")), equal_to([700]))


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

    assert_that(await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)), equal_to(0))
    assert_that(await fake_telegram_server.requests(method="sendPhoto"), empty())
    assert_that(await fake_yandex_server.requests(), empty())


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

    assert_that(await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)), equal_to(1))

    send_photo_requests = await fake_telegram_server.requests(method="sendPhoto")
    assert_that(
        [request["payload"]["photo"] for request in send_photo_requests],
        equal_to([f"{fake_yandex_server.base_url}/download/pending.png"]),
    )
    assert_that(
        sum(request["method"] == "resources/download" for request in await fake_yandex_server.requests()),
        equal_to(1),
    )
    assert_that(
        await read_functional_category_media_states(),
        equal_to(
            {
                "morning/pending.png": ("ready", "functional-photo-file-id"),
                "morning/ready.png": ("ready", "functional-ready-file-id"),
            }
        ),
    )


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

    assert_that(await run_subscription_broadcasts_at(datetime(2026, 8, 16, 3, 0, tzinfo=UTC)), equal_to(2))

    send_photo_requests = await fake_telegram_server.requests(method="sendPhoto")
    assert_that(sorted(_sent_chat_ids(send_photo_requests)), equal_to([701, 702]))
    sent_media = [request["payload"]["photo"] for request in send_photo_requests]
    assert_that(sent_media.count(f"{fake_yandex_server.base_url}/download/image.png"), equal_to(1))
    assert_that(sent_media.count("functional-photo-file-id"), equal_to(1))
    assert_that(
        sum(request["method"] == "resources/download" for request in await fake_yandex_server.requests()),
        equal_to(1),
    )


async def test_subscription_broadcasts_keep_independent_cycles_across_catalog_changes(
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/cycle", time(10), "cycle"),))
    for user_id in (700, 701):
        await create_user_subscription(
            user_id=user_id,
            subscription_type_id=1,
            timezone_offset_minutes=7 * 60,
        )

    initial_names = ["first.png", "second.png", "third.png"]
    await fake_yandex_server.configure_directory("cycle", images=[{"name": name} for name in initial_names])
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids({f"cycle/{name}": f"telegram-{name}" for name in initial_names})
    await fake_telegram_server.reset()
    await fake_yandex_server.reset()

    for day in range(1, 5):
        assert_that(
            await run_subscription_broadcasts_at(datetime(2026, 9, day, 3, 0, tzinfo=UTC)),
            equal_to(2),
        )

    initial_sequences = _sent_media_by_user(await fake_telegram_server.requests(method="sendPhoto"))
    expected_initial = {f"telegram-{name}" for name in initial_names}
    for sequence in initial_sequences.values():
        assert_that(set(sequence[:3]), equal_to(expected_initial))
        assert_that(sequence[3] == sequence[2], equal_to(False))

    all_names = [*initial_names, "added.png"]
    await fake_yandex_server.configure_directory("cycle", images=[{"name": name} for name in all_names])
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids({"cycle/added.png": "telegram-added.png"})
    await fake_yandex_server.reset()

    for day in range(5, 8):
        assert_that(
            await run_subscription_broadcasts_at(datetime(2026, 9, day, 3, 0, tzinfo=UTC)),
            equal_to(2),
        )

    extended_sequences = _sent_media_by_user(await fake_telegram_server.requests(method="sendPhoto"))
    expected_extended = {f"telegram-{name}" for name in all_names}
    for sequence in extended_sequences.values():
        assert_that(set(sequence[3:7]), equal_to(expected_extended))

    assert_that(
        await run_subscription_broadcasts_at(datetime(2026, 9, 8, 3, 0, tzinfo=UTC)),
        equal_to(2),
    )
    remaining_names = [name for name in all_names if name != "third.png"]
    await fake_yandex_server.configure_directory("cycle", images=[{"name": name} for name in remaining_names])
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    for day in range(9, 13):
        assert_that(
            await run_subscription_broadcasts_at(datetime(2026, 9, day, 3, 0, tzinfo=UTC)),
            equal_to(2),
        )

    final_sequences = _sent_media_by_user(await fake_telegram_server.requests(method="sendPhoto"))
    for sequence in final_sequences.values():
        assert_that("telegram-third.png" in sequence[8:], equal_to(False))
        assert_that(
            [current == previous for previous, current in zip(sequence[7:-1], sequence[8:], strict=True)],
            equal_to([False, False, False, False]),
        )
    assert_that(await fake_yandex_server.requests(), empty())


async def test_ordinary_and_scheduled_delivery_share_one_user_cycle(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    run_subscription_broadcasts_at: Callable[[datetime], Awaitable[int]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
    read_functional_user_media_cycle: Callable[[int, int], Awaitable[tuple[str | None, dict[str, str]] | None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/cycle", time(10), "cycle"),))
    await create_user_subscription(user_id=42, subscription_type_id=1, timezone_offset_minutes=7 * 60)
    image_names = ["first.png", "second.png"]
    await fake_yandex_server.configure_directory("cycle", images=[{"name": name} for name in image_names])
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids({f"cycle/{name}": f"telegram-{name}" for name in image_names})
    await fake_telegram_server.reset()
    await fake_yandex_server.reset()

    await fake_telegram_server.push_message(text="/cycle")
    ordinary_request = await fake_telegram_server.wait_for_request("editMessageMedia")
    ordinary_media = ordinary_request["payload"]["media"]["media"]
    await _wait_for_cycle_shown_count(
        read_functional_user_media_cycle,
        user_id=42,
        subscription_type_id=1,
        expected=1,
    )

    await fake_telegram_server.reset()
    assert_that(
        await run_subscription_broadcasts_at(datetime(2026, 9, 1, 3, 0, tzinfo=UTC)),
        equal_to(1),
    )
    scheduled_request = await fake_telegram_server.wait_for_request("sendPhoto")
    scheduled_media = scheduled_request["payload"]["photo"]

    assert_that(scheduled_media == ordinary_media, equal_to(False))
    cycle = await read_functional_user_media_cycle(42, 1)
    assert cycle is not None
    assert_that(set(cycle[1].values()), equal_to({"shown"}))
    assert_that(len(cycle[1]), equal_to(2))
    assert_that(await fake_yandex_server.requests(), empty())


def _sent_chat_ids(requests: list[dict[str, Any]]) -> list[int]:
    return [int(request["payload"]["chat_id"]) for request in requests]


def _sent_media_by_user(requests: list[dict[str, Any]]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for request in requests:
        result.setdefault(int(request["payload"]["chat_id"]), []).append(request["payload"]["photo"])
    return result


async def _wait_for_cycle_shown_count(
    read_cycle: Callable[[int, int], Awaitable[tuple[str | None, dict[str, str]] | None]],
    *,
    user_id: int,
    subscription_type_id: int,
    expected: int,
    timeout: float = 10,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        cycle = await read_cycle(user_id, subscription_type_id)
        if cycle is not None and sum(status == "shown" for status in cycle[1].values()) == expected:
            return
        await asyncio.sleep(0.1)

    raise TimeoutError(f"Cycle did not reach {expected} shown entries in {timeout} seconds")

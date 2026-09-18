import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, time
from typing import Any

import asyncpg
import pytest
from hamcrest import assert_that, empty, equal_to, has_entries, has_length

from tests.functional.conftest import (
    DependencyPorts,
    FakeYandexServer,
    FunctionalSubscriptionType,
    MainKeyboardBot,
    _create_postgres_connection,
    _wait_until_ready,
)


@pytest.fixture(autouse=True)
async def reset_state(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state(())


@pytest.fixture
async def keyboard_database(docker_compose: DependencyPorts) -> AsyncIterator[asyncpg.Connection]:
    connection = await _create_postgres_connection(docker_compose)

    try:
        await connection.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        await connection.execute("SELECT pg_stat_statements_reset()")

        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def keyboard_bot(
    start_main_keyboard_bot: Callable[[], AbstractAsyncContextManager[MainKeyboardBot]],
) -> AsyncIterator[MainKeyboardBot]:
    async with start_main_keyboard_bot() as bot:
        yield bot


async def test_parallel_commands_share_one_snapshot_and_refresh_once_at_fixed_ttl(
    start_main_keyboard_bot: Callable[[], AbstractAsyncContextManager[MainKeyboardBot]],
    keyboard_database: asyncpg.Connection,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/old", None, "old"),))
    await set_functional_administrator(user_id=1001)

    async with start_main_keyboard_bot() as bot:

        async def batch() -> dict[int, dict[str, Any]]:
            await bot.telegram.reset()
            await asyncio.gather(*(bot.telegram.push_message(user_id=i, text="/timezone") for i in range(1000, 1020)))

            requests = await asyncio.gather(
                *(
                    bot.telegram.wait_for_request("sendMessage", predicate=lambda r, i=i: r["payload"]["chat_id"] == i)
                    for i in range(1000, 1020)
                )
            )

            return {r["payload"]["chat_id"]: r["payload"] for r in requests}

        first = await batch()
        assert await _snapshot_counts(keyboard_database) == (1, 1)
        for user_id, payload in first.items():
            expected = ["Админ-панель"] if user_id == 1001 else []
            assert_that(_buttons(payload), equal_to(expected + ["Подписки", "/old"]))
            assert_that(payload["reply_markup"], has_entries(selective=False, resize_keyboard=True))

        await set_functional_administrator(user_id=1001, enabled=False)
        await set_functional_administrator(user_id=1002)
        await keyboard_database.execute("UPDATE subscription_types SET name = '/new' WHERE id = 1")

        for seconds in (30.0, 29.0):
            await bot.advance(seconds)
            warmed = await batch()
            assert_that(
                {i: _buttons(p) for i, p in warmed.items()}, equal_to({i: _buttons(p) for i, p in first.items()})
            )
        assert await _snapshot_counts(keyboard_database) == (1, 1)

        await bot.advance(1)
        refreshed = await batch()
        assert await _snapshot_counts(keyboard_database) == (2, 2)
        for user_id, payload in refreshed.items():
            expected = ["Админ-панель"] if user_id == 1002 else []
            assert_that(_buttons(payload), equal_to(expected + ["Подписки", "/new"]))


@pytest.mark.parametrize("chat_id,chat_type", [(-42, "group"), (-100123, "channel")])
async def test_public_chat_commands_do_not_receive_private_keyboard(
    chat_id: int, chat_type: str, keyboard_bot: MainKeyboardBot, keyboard_database: asyncpg.Connection
) -> None:
    await keyboard_bot.telegram.push_message(text="/timezone", chat_id=chat_id, chat_type=chat_type)
    request = await keyboard_bot.telegram.wait_for_request("sendMessage")

    assert "reply_markup" not in request["payload"]
    assert await _snapshot_counts(keyboard_database) == (0, 0)


@pytest.mark.parametrize("text", ["/start", "/help", "/timezone", "/timezone +04:00", "/timezone invalid"])
async def test_private_commands_attach_main_keyboard(text: str, keyboard_bot: MainKeyboardBot) -> None:
    await keyboard_bot.telegram.push_message(text=text)
    request = await keyboard_bot.telegram.wait_for_request("sendMessage")

    assert_that(_buttons(request["payload"]), equal_to(["Подписки"]))
    assert request["payload"]["reply_markup"]["selective"] is False


async def test_category_changes_refresh_on_timezone_and_ordinary_without_another_start(
    start_main_keyboard_bot: Callable[[], AbstractAsyncContextManager[MainKeyboardBot]],
    keyboard_database: asyncpg.Connection,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await seed_functional_subscription_types(
        (
            FunctionalSubscriptionType(1, "/keep", time(8), "keep"),
            FunctionalSubscriptionType(2, "/remove", time(9), "remove"),
            FunctionalSubscriptionType(3, "/instant", None, "instant", is_active=False),
            FunctionalSubscriptionType(4, "/reorder", time(10), "reorder"),
        )
    )

    async with start_main_keyboard_bot() as bot:

        async def catalog_ready() -> None:
            assert await keyboard_database.fetchval("SELECT count(*) FROM category_media") == 4

        await _wait_until_ready(catalog_ready, "keyboard media catalog")
        await bot.telegram.push_message(text="/start")
        first = await bot.telegram.wait_for_request("sendMessage")
        assert_that(_buttons(first["payload"]), equal_to(["Подписки", "/keep", "/remove", "/reorder"]))

        await keyboard_database.execute("UPDATE subscription_types SET is_active = false WHERE id = 2")
        await keyboard_database.execute("UPDATE subscription_types SET is_active = true, name = '/added' WHERE id = 3")
        await keyboard_database.execute("UPDATE subscription_types SET time = '07:00' WHERE id = 4")

        await bot.telegram.reset()
        await bot.advance(59.999)
        await bot.telegram.push_message(text="/timezone")
        before = await bot.telegram.wait_for_request("sendMessage")
        assert_that(_buttons(before["payload"]), equal_to(_buttons(first["payload"])))

        await bot.telegram.reset()
        await bot.advance(0.001)
        await bot.telegram.push_message(text="/timezone")
        after = await bot.telegram.wait_for_request("sendMessage")
        assert_that(_buttons(after["payload"]), equal_to(["Подписки", "/reorder", "/keep", "/added"]))

        await bot.telegram.reset()
        await bot.telegram.push_message(text="/keep")
        placeholder = await bot.telegram.wait_for_request("sendMessage")
        edited = await bot.telegram.wait_for_request("editMessageMedia")

        assert_that(_buttons(placeholder["payload"]), equal_to(_buttons(after["payload"])))
        assert "reply_markup" not in edited["payload"]
        assert_that(await bot.telegram.requests(method="sendMessage"), has_length(1))

        async def delivery_confirmed() -> None:
            row = await keyboard_database.fetchrow(
                """
                SELECT media.status, media.telegram_file_id, entry.reservation_token
                FROM category_media AS media
                JOIN user_media_cycle_entries AS entry ON entry.media_id = media.id
                WHERE entry.user_id = 42 AND media.source_path = 'keep/image.png'
                """
            )

            assert row is not None
            assert_that(tuple(row), equal_to(("ready", "functional-photo-file-id", None)))

        await _wait_until_ready(delivery_confirmed, "ordinary delivery confirmation")


async def test_subscription_inline_ui_and_callback_pagination_do_not_add_messages(
    start_main_keyboard_bot: Callable[[], AbstractAsyncContextManager[MainKeyboardBot]],
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await seed_functional_subscription_types(
        tuple(FunctionalSubscriptionType(i, f"/cat{i}", time(8), f"cat{i}") for i in range(1, 10))
    )

    async with start_main_keyboard_bot() as bot:
        await bot.telegram.push_message(text="/subscriptions")
        shown = await bot.telegram.wait_for_request("sendMessage")
        assert "inline_keyboard" in shown["payload"]["reply_markup"]

        await bot.telegram.reset()
        await bot.telegram.push_callback_query(data="subscription_page:1")
        edited = await bot.telegram.wait_for_request("editMessageReplyMarkup")
        await bot.telegram.wait_for_request("answerCallbackQuery")

        assert "inline_keyboard" in edited["payload"]["reply_markup"]
        assert_that(await bot.telegram.requests(method="sendMessage"), empty())


@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize(
    "mime_type,method,file_name,file_id",
    [
        ("image/png", "sendPhoto", "image.png", "functional-photo-file-id"),
        ("image/gif", "sendAnimation", "image.gif", "functional-animation-file-id"),
        ("video/mp4", "sendVideo", "clip.mp4", "functional-video-file-id"),
    ],
)
async def test_background_media_attaches_recipients_keyboard_and_preserves_delivery(
    is_admin: bool,
    mime_type: str,
    method: str,
    file_name: str,
    file_id: str,
    start_main_keyboard_bot: Callable[[], AbstractAsyncContextManager[MainKeyboardBot]],
    keyboard_database: asyncpg.Connection,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/scheduled", time(10), "scheduled"),))
    await create_user_subscription(user_id=700, subscription_type_id=1)
    await set_functional_administrator(user_id=700, enabled=is_admin)
    await fake_yandex_server.configure_directory("scheduled", images=[{"name": file_name, "mime_type": mime_type}])

    async with start_main_keyboard_bot() as bot:

        async def catalog_ready() -> None:
            assert await keyboard_database.fetchval("SELECT count(*) FROM category_media") == 1

        await _wait_until_ready(catalog_ready, "scheduled keyboard media catalog")
        await bot.advance(wall_now=datetime(2030, 9, 19, 3, tzinfo=UTC))
        request = await bot.telegram.wait_for_request(method)

        assert_that(
            _buttons(request["payload"]), equal_to((["Админ-панель"] if is_admin else []) + ["Подписки", "/scheduled"])
        )
        assert request["payload"]["chat_id"] == 700
        assert request["payload"]["reply_markup"]["selective"] is False

        async def delivery_confirmed() -> None:
            row = await keyboard_database.fetchrow(
                "SELECT status, telegram_file_id FROM category_media WHERE source_path = $1", f"scheduled/{file_name}"
            )

            assert row is not None
            assert_that(tuple(row), equal_to(("ready", file_id)))

        await _wait_until_ready(delivery_confirmed, "scheduled delivery confirmation")
        assert_that(await bot.telegram.requests(method=method), has_length(1))
        assert_that(await bot.telegram.requests(method="sendMessage"), empty())


async def test_copied_admin_notifications_choose_recipient_roles_and_keep_statuses(
    start_main_keyboard_bot: Callable[[], AbstractAsyncContextManager[MainKeyboardBot]],
    create_functional_user: Callable[..., Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    create_functional_admin_broadcast: Callable[..., Awaitable[int]],
    read_admin_broadcast_state: Callable[[int], Awaitable[tuple[str, list[tuple[int, str]]]]],
) -> None:
    for user_id in (42, 700):
        await create_functional_user(user_id=user_id)
    await set_functional_administrator(user_id=42)

    async with start_main_keyboard_bot() as bot:

        async def batch(day: int, administrator_id: int) -> None:
            broadcast_id = await create_functional_admin_broadcast(
                scheduled_local_at=datetime(2030, 9, day, 10), timezone_offset_minutes=420
            )
            await bot.advance(60 if day == 20 else 0, wall_now=datetime(2030, 9, day, 3, tzinfo=UTC))

            for user_id in (42, 700):
                request = await bot.telegram.wait_for_request(
                    "copyMessage", predicate=lambda r, i=user_id: r["payload"]["chat_id"] == i
                )
                payload = request["payload"]

                assert payload["from_chat_id"] == 42
                expected = ["Админ-панель"] if user_id == administrator_id else []
                assert_that(_buttons(payload), equal_to(expected + ["Подписки"]))

            async def completed() -> None:
                assert_that(
                    await read_admin_broadcast_state(broadcast_id),
                    equal_to(("completed", [(42, "sent"), (700, "sent")])),
                )

            await _wait_until_ready(completed, "copied notification completion")
            assert_that(await bot.telegram.requests(method="copyMessage"), has_length(2))
            assert_that(await bot.telegram.requests(method="sendMessage"), empty())

        await batch(19, 42)

        await set_functional_administrator(user_id=42, enabled=False)
        await set_functional_administrator(user_id=700)
        await bot.telegram.reset()
        await batch(20, 700)


async def _snapshot_counts(connection: asyncpg.Connection) -> tuple[int, int]:
    categories = administrators = 0

    for row in await connection.fetch("SELECT query, calls FROM pg_stat_statements WHERE query LIKE 'SELECT%'"):
        query = row["query"].lower()
        if (
            "from subscription_types" in query
            and "where subscription_types.is_active" in query
            and "time is not null" not in query
        ):
            categories += row["calls"]
        if "from administrators" in query and "where" not in query:
            administrators += row["calls"]

    return categories, administrators


def _buttons(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["keyboard"] for button in row]

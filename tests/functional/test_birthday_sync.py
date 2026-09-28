import asyncio
import signal
from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import time

import pytest
from hamcrest import assert_that, contains_string, equal_to

from tests.functional.conftest import (
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalBotStarter,
    FunctionalSubscriptionType,
)

_SYNC_CALLBACK = "admin_panel:synchronize_media"
_BIRTHDAY_SYNC_LEASE_KEY = "birthday-sync:telegram-profiles"
type UserState = tuple[int, bool, int | None, int | None, str | None]


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(())


async def test_admin_sync_reconciles_telegram_birthdays_and_skips_manual_values(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    seed_birthday_sync_users: Callable[
        [list[tuple[int, int, bool, tuple[int, int] | None, str | None]]], Awaitable[None]
    ],
    read_birthday_sync_users: Callable[[], Awaitable[dict[int, UserState]]],
) -> None:
    await seed_birthday_sync_users(
        [
            (1, 60, True, None, None),
            (2, 120, True, (1, 1), "telegram"),
            (3, 180, True, (3, 3), "telegram"),
            (4, 240, True, (4, 4), "telegram"),
            (5, 300, True, None, None),
            (7, 360, False, None, None),
            (42, 420, True, (12, 31), "manual"),
        ]
    )
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.set_get_chat_responses(
        {
            1: [{"birthdate": {"day": 29, "month": 2, "year": 2000}}],
            2: [{"birthdate": {"day": 2, "month": 2}}],
            3: [{"birthdate": {"day": 3, "month": 3}}],
            4: [{}],
            5: [{}],
            7: [{"birthdate": {"day": 7, "month": 7, "year": 1999}}],
        }
    )

    result = await _run_admin_sync(fake_telegram_server, message_id=201)

    states = await read_birthday_sync_users()
    assert_that(states[1], equal_to((60, True, 2, 29, "telegram")))
    assert_that(states[2], equal_to((120, True, 2, 2, "telegram")))
    assert_that(states[3], equal_to((180, True, 3, 3, "telegram")))
    assert_that(states[4], equal_to((240, True, None, None, None)))
    assert_that(states[5], equal_to((300, True, None, None, None)))
    assert_that(states[7], equal_to((360, False, 7, 7, "telegram")))
    assert_that(states[42], equal_to((420, True, 12, 31, "manual")))

    get_chat_requests = await fake_telegram_server.requests(method="getChat")
    assert_that({int(request["payload"]["chat_id"]) for request in get_chat_requests}, equal_to({1, 2, 3, 4, 5, 7}))
    for expected_line in (
        "Рассмотрено пользователей: <b>7</b>",
        "Получено дат из Telegram: <b>4</b>",
        "Создано дат: <b>2</b>",
        "Обновлено дат: <b>1</b>",
        "Очищено дат: <b>1</b>",
        "Без изменений: <b>2</b>",
        "Пропущено ручных значений: <b>1</b>",
        "Профилей без даты: <b>2</b>",
        "Пользователей с ошибками: <b>0</b>",
    ):
        assert_that(result, contains_string(expected_line))


async def test_admin_sync_retries_429_once_and_isolates_profile_errors(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    seed_birthday_sync_users: Callable[
        [list[tuple[int, int, bool, tuple[int, int] | None, str | None]]], Awaitable[None]
    ],
    read_birthday_sync_users: Callable[[], Awaitable[dict[int, UserState]]],
) -> None:
    await seed_birthday_sync_users(
        [
            (1, 60, True, None, None),
            (2, 120, True, (2, 2), "telegram"),
            (3, -300, False, (3, 3), "telegram"),
            (4, 240, True, (4, 4), "telegram"),
            (5, 300, True, None, None),
            (42, 420, True, (12, 31), "manual"),
        ]
    )
    await set_functional_administrator(user_id=42)
    retry = {"error_code": 429, "description": "Too Many Requests", "retry_after": 1}
    await fake_telegram_server.set_get_chat_responses(
        {
            1: [retry, {"birthdate": {"day": 1, "month": 1}}],
            2: [retry, retry],
            3: [{"error_code": 403, "description": "Forbidden"}],
            4: [{"type": "group"}],
            5: [{"birthdate": {"day": 5, "month": 5}}],
        }
    )

    result = await _run_admin_sync(fake_telegram_server, message_id=202)

    states = await read_birthday_sync_users()
    assert_that(states[1], equal_to((60, True, 1, 1, "telegram")))
    assert_that(states[2], equal_to((120, True, 2, 2, "telegram")))
    assert_that(states[3], equal_to((-300, False, 3, 3, "telegram")))
    assert_that(states[4], equal_to((240, True, 4, 4, "telegram")))
    assert_that(states[5], equal_to((300, True, 5, 5, "telegram")))
    assert_that(states[42], equal_to((420, True, 12, 31, "manual")))

    request_counts: dict[int, int] = {}
    for request in await fake_telegram_server.requests(method="getChat"):
        user_id = int(request["payload"]["chat_id"])
        request_counts[user_id] = request_counts.get(user_id, 0) + 1
    assert_that(request_counts, equal_to({1: 2, 2: 2, 3: 1, 4: 1, 5: 1}))
    assert_that(result, contains_string("Получено дат из Telegram: <b>2</b>"))
    assert_that(result, contains_string("Создано дат: <b>2</b>"))
    assert_that(result, contains_string("Пользователей с ошибками: <b>3</b>"))


async def test_sync_bounds_concurrency_uses_next_page_and_preserves_concurrent_manual_value(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    seed_birthday_sync_users: Callable[
        [list[tuple[int, int, bool, tuple[int, int] | None, str | None]]], Awaitable[None]
    ],
    read_birthday_sync_users: Callable[[], Awaitable[dict[int, UserState]]],
) -> None:
    users = [(user_id, 420, True, None, None) for user_id in range(1000, 1101)]
    users.append((42, 420, True, (12, 31), "manual"))
    await seed_birthday_sync_users(users)
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.block_method("getChat")

    await fake_telegram_server.push_callback_query(data=_SYNC_CALLBACK, message_id=301)
    stats = await fake_telegram_server.wait_for_requests("getChat", count=5, active=5)
    assert_that(stats["max_active"], equal_to(5))

    await fake_telegram_server.push_message(text="/birthday 08.09", user_id=1000)
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: (
            request["payload"].get("chat_id") == 1000 and "День рождения сохранён" in request["payload"].get("text", "")
        ),
    )

    await fake_telegram_server.push_callback_query(data=_SYNC_CALLBACK, message_id=302)
    second_result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 302
            and "Синхронизация профилей уже выполняется" in request["payload"].get("text", "")
        ),
    )
    assert_that(second_result["payload"]["text"], contains_string("Новый запуск не начат"))

    await fake_telegram_server.release_method("getChat")
    await fake_telegram_server.wait_for_requests("getChat", count=101)
    first_result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 301
            and "Синхронизация профилей завершена" in request["payload"].get("text", "")
        ),
    )

    final_stats = await fake_telegram_server.get_chat_stats()
    assert_that(final_stats, equal_to({"count": 101, "active": 0, "max_active": 5}))
    assert_that(first_result["payload"]["text"], contains_string("Рассмотрено пользователей: <b>102</b>"))
    assert_that(first_result["payload"]["text"], contains_string("Пропущено ручных значений: <b>2</b>"))
    assert_that(first_result["payload"]["text"], contains_string("Профилей без даты: <b>101</b>"))
    assert_that((await read_birthday_sync_users())[1000], equal_to((420, True, 9, 8, "manual")))


async def test_first_background_run_synchronizes_media_and_profiles(
    fake_yandex_server: FakeYandexServer,
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    seed_birthday_sync_users: Callable[
        [list[tuple[int, int, bool, tuple[int, int] | None, str | None]]], Awaitable[None]
    ],
    wait_for_user_birthday_state: Callable[[int, tuple[int | None, int | None, str | None]], Awaitable[None]],
    read_functional_category_media_states: Callable[[], Awaitable[dict[str, tuple[str, str | None]]]],
    start_functional_bot_process: FunctionalBotStarter,
) -> None:
    subscription_type = FunctionalSubscriptionType(1, "/birthday", time(10), "birthday")
    await reset_functional_state((subscription_type,))
    await fake_yandex_server.configure_directory("birthday", images=[{"name": "birthday.png"}])
    await seed_birthday_sync_users([(1, 420, True, None, None)])
    responses = {1: [{"birthdate": {"day": 6, "month": 5, "year": 1988}}]}
    async with start_functional_bot_process(get_chat_responses=responses) as running:
        await running.telegram.wait_for_requests("getChat", count=1)
        await wait_for_user_birthday_state(1, (5, 6, "telegram"))

    await fake_yandex_server.wait_for_request(
        "resources",
        predicate=lambda request: request["params"].get("path") == "app:/birthday",
    )
    assert_that(
        await read_functional_category_media_states(),
        equal_to({"birthday/birthday.png": ("pending", None)}),
    )


async def test_cancellation_releases_profile_lease_for_next_process(
    seed_birthday_sync_users: Callable[
        [list[tuple[int, int, bool, tuple[int, int] | None, str | None]]], Awaitable[None]
    ],
    wait_for_user_birthday_state: Callable[[int, tuple[int | None, int | None, str | None]], Awaitable[None]],
    redis_key_exists: Callable[[str], Awaitable[bool]],
    start_functional_bot_process: FunctionalBotStarter,
) -> None:
    await seed_birthday_sync_users([(1, 420, True, None, None)])

    async with start_functional_bot_process(block_get_chat=True) as running:
        await running.telegram.wait_for_requests("getChat", count=1, active=1)
        assert await redis_key_exists(_BIRTHDAY_SYNC_LEASE_KEY)

        running.process.send_signal(signal.SIGINT)
        await asyncio.wait_for(running.process.wait(), timeout=10)
        assert not await redis_key_exists(_BIRTHDAY_SYNC_LEASE_KEY)

        await running.telegram.release_method("getChat")
        await running.telegram.wait_for_requests("getChat", count=1, active_at_most=0)

    responses = {1: [{"birthdate": {"day": 1, "month": 4}}]}
    async with start_functional_bot_process(get_chat_responses=responses) as running:
        await running.telegram.wait_for_requests("getChat", count=1)
        await wait_for_user_birthday_state(1, (4, 1, "telegram"))


async def test_media_category_failure_does_not_cancel_profile_updates(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    seed_birthday_sync_users: Callable[
        [list[tuple[int, int, bool, tuple[int, int] | None, str | None]]], Awaitable[None]
    ],
    read_birthday_sync_users: Callable[[], Awaitable[dict[int, UserState]]],
) -> None:
    await reset_functional_state(
        (
            FunctionalSubscriptionType(1, "/working", time(10), "working"),
            FunctionalSubscriptionType(2, "/broken", time(11), "broken"),
        )
    )
    await fake_yandex_server.configure_directory("working", images=[{"name": "working.png"}])
    await fake_yandex_server.configure_directory("broken", fail=True)
    await seed_birthday_sync_users(
        [
            (1, 420, True, None, None),
            (42, 420, True, (12, 31), "manual"),
        ]
    )
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.set_get_chat_responses({1: [{"birthdate": {"day": 9, "month": 10}}]})

    result = await _run_admin_sync(fake_telegram_server, message_id=401)

    assert_that(result, contains_string("Синхронизация медиа завершена частично"))
    assert_that(result, contains_string("Категорий с ошибками: <b>1</b>"))
    assert_that(result, contains_string("Синхронизация профилей завершена"))
    assert_that((await read_birthday_sync_users())[1], equal_to((420, True, 10, 9, "telegram")))


async def _run_admin_sync(fake_telegram_server: FakeTelegramServer, *, message_id: int) -> str:
    await fake_telegram_server.push_callback_query(data=_SYNC_CALLBACK, message_id=message_id)
    result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == message_id
            and "Синхронизация профилей" in request["payload"].get("text", "")
            and "Рассмотрено пользователей" in request["payload"].get("text", "")
        ),
        timeout=30,
    )
    return str(result["payload"]["text"])

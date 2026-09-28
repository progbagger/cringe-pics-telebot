from asyncio import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from hamcrest import assert_that, contains_string, equal_to

from tests.functional.conftest import FakeTelegramServer, FunctionalSubscriptionType


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(())


async def test_birthday_command_views_creates_replaces_and_clears_manual_value(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    read_user_birthday_state: Callable[[int], Awaitable[tuple[int | None, int | None, str | None] | None]],
) -> None:
    empty_response = await _send_command(fake_telegram_server, "/birthday", "пока не указан")
    assert_that(empty_response["payload"]["text"], contains_string("/birthday 31.12"))
    assert_that(await read_user_birthday_state(42), equal_to((None, None, None)))

    saved_response = await _send_command(fake_telegram_server, "/birthday 29.02", "сохранён")
    assert_that(saved_response["payload"]["text"], contains_string("<b>29.02</b>"))
    assert_that(saved_response["payload"]["text"], contains_string("приоритет"))
    assert_that(await read_user_birthday_state(42), equal_to((2, 29, "manual")))

    viewed_response = await _send_command(fake_telegram_server, "/birthday", "Твой день рождения")
    assert_that(viewed_response["payload"]["text"], contains_string("<b>29.02</b>"))
    assert_that(viewed_response["payload"]["text"], contains_string("вручную"))

    await _send_command(fake_telegram_server, "/birthday 31.12", "сохранён")
    assert_that(await read_user_birthday_state(42), equal_to((12, 31, "manual")))

    cleared_response = await _send_command(fake_telegram_server, "/birthday clear", "удалён")
    assert_that(cleared_response["payload"]["text"], contains_string("профиля Telegram"))
    assert_that(await read_user_birthday_state(42), equal_to((None, None, None)))


@pytest.mark.parametrize(
    "invalid_value",
    ["1.02", "01.2", "31.04", "01.02.2000", "01.02 extra", "clear extra"],
)
async def test_invalid_birthday_input_preserves_saved_value(
    invalid_value: str,
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    read_user_birthday_state: Callable[[int], Awaitable[tuple[int | None, int | None, str | None] | None]],
) -> None:
    await _send_command(fake_telegram_server, "/birthday 15.08", "сохранён")

    response = await _send_command(fake_telegram_server, f"/birthday {invalid_value}", "Не удалось распознать")

    assert_that(response["payload"]["text"], contains_string("<code>DD.MM</code>"))
    assert_that(await read_user_birthday_state(42), equal_to((8, 15, "manual")))


async def test_birthday_command_shows_and_clears_telegram_value(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    read_user_birthday_state: Callable[[int], Awaitable[tuple[int | None, int | None, str | None] | None]],
    set_user_birthday_state: Callable[[int, tuple[int, int] | None, str | None], Awaitable[None]],
) -> None:
    await set_user_birthday_state(42, (7, 9), "telegram")

    response = await _send_command(fake_telegram_server, "/birthday", "Твой день рождения")
    assert_that(response["payload"]["text"], contains_string("<b>09.07</b>"))
    assert_that(response["payload"]["text"], contains_string("из Telegram"))

    await _send_command(fake_telegram_server, "/birthday clear", "удалён")
    assert_that(await read_user_birthday_state(42), equal_to((None, None, None)))


@pytest.mark.parametrize("command", ["/start", "/help"])
async def test_start_and_help_describe_birthday_privacy(
    command: str,
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
) -> None:
    response = await _send_command(fake_telegram_server, command, "Что умеет бот")

    assert_that(response["payload"]["text"], contains_string("<code>/birthday [DD.MM|clear]</code>"))
    assert_that(response["payload"]["text"], contains_string("только день и месяц"))
    assert_that(response["payload"]["text"], contains_string("добровольный"))


async def _send_command(
    fake_telegram_server: FakeTelegramServer,
    command: str,
    expected_text: str,
) -> dict[str, Any]:
    await fake_telegram_server.reset()
    await fake_telegram_server.push_message(text=command)
    return await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: expected_text in request["payload"].get("text", ""),
    )

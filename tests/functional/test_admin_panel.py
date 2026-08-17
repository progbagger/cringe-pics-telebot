from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import pytest

from cringe_pics_telebot.bot.admin_callback_data import AdminAction, AdminCallbackData
from tests.functional.conftest import (
    SEEDED_SUBSCRIPTION_TYPES,
    FakeTelegramServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(SEEDED_SUBSCRIPTION_TYPES)


async def test_admin_access_and_reply_button_follow_database_without_restart(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.push_message(text="/start", first_name="Admin")
    start_request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Что умеет бот" in request["payload"].get("text", ""),
    )
    assert _reply_keyboard_button_texts(start_request["payload"])[0] == "Админ-панель"

    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Админ-панель" in request["payload"].get("text", ""),
    )

    await set_functional_administrator(user_id=42, enabled=False)
    await fake_telegram_server.reset()
    await fake_telegram_server.push_message(text="/admin")
    non_admin_request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Что умеет бот" in request["payload"].get("text", ""),
    )
    assert "Админ-панель" not in _reply_keyboard_button_texts(non_admin_request["payload"])

    await fake_telegram_server.reset()
    await fake_telegram_server.push_message(text="Админ-панель")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Что умеет бот" in request["payload"].get("text", ""),
    )


async def test_non_admin_cannot_forge_admin_callback(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
) -> None:
    await fake_telegram_server.push_callback_query(
        data=_admin_callback(AdminAction.new_broadcast),
        user_id=999,
    )
    await fake_telegram_server.push_message(text="synchronization", user_id=999)
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("chat_id") == 999,
    )

    assert not await fake_telegram_server.requests(method="editMessageText")
    assert not await fake_telegram_server.requests(method="answerCallbackQuery")


async def test_empty_broadcast_list_starts_creation_and_validates_schedule(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_admin_broadcasts: Callable[[], Awaitable[list[dict[str, Any]]]],
) -> None:
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Админ-панель" in request["payload"].get("text", ""),
    )

    await fake_telegram_server.push_callback_query(data=_admin_callback(AdminAction.broadcasts))
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Новая рассылка" in request["payload"].get("text", ""),
    )

    await fake_telegram_server.push_message(text="Важное сообщение")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "ДД.ММ.ГГГГ" in request["payload"].get("text", ""),
    )

    await fake_telegram_server.push_message(text="не дата")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Не удалось распознать" in request["payload"].get("text", ""),
    )
    assert not await read_admin_broadcasts()

    await fake_telegram_server.push_message(text="20.08.2099 10:00")
    confirmation = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Рассылка запланирована" in request["payload"].get("text", ""),
    )

    broadcasts = await read_admin_broadcasts()
    assert len(broadcasts) == 1
    assert broadcasts[0]["created_by_user_id"] == 42
    assert broadcasts[0]["source_chat_id"] == 42
    assert broadcasts[0]["source_message_id"] == 1
    assert broadcasts[0]["scheduled_local_at"] == datetime(2099, 8, 20, 10, 0)
    assert broadcasts[0]["timezone_offset_minutes"] is None
    assert broadcasts[0]["status"] == "scheduled"
    assert "Новая рассылка" in _inline_keyboard_button_texts(confirmation["payload"])


async def test_admin_edits_and_soft_deletes_existing_broadcast(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    create_functional_admin_broadcast: Callable[..., Awaitable[int]],
    read_admin_broadcast: Callable[[int], Awaitable[dict[str, Any] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    broadcast_id = await create_functional_admin_broadcast(
        scheduled_local_at=datetime(2099, 8, 20, 10, 0),
    )
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request("sendMessage")

    await fake_telegram_server.push_callback_query(data=_admin_callback(AdminAction.broadcasts))
    broadcast_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Запланированные рассылки" in request["payload"].get("text", ""),
    )
    assert _inline_keyboard_button_texts(broadcast_list["payload"]) == [
        "20.08 10:00 · локально",
        "✏️",
        "🗑",
        "Новая рассылка",
        "Назад",
    ]

    await fake_telegram_server.push_callback_query(
        data=_admin_callback(AdminAction.edit_schedule, broadcast_id),
        message_id=101,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Введите новую дату" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="21.08.2099 11:30 +04:00")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Дата и время рассылки обновлены" in request["payload"].get("text", ""),
    )
    broadcast = await read_admin_broadcast(broadcast_id)
    assert broadcast is not None
    assert broadcast["scheduled_local_at"] == datetime(2099, 8, 21, 11, 30)
    assert broadcast["timezone_offset_minutes"] == 240

    await fake_telegram_server.push_callback_query(
        data=_admin_callback(AdminAction.edit_message, broadcast_id),
        message_id=102,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "новое сообщение" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="Новое содержимое")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Сообщение рассылки обновлено" in request["payload"].get("text", ""),
    )
    broadcast = await read_admin_broadcast(broadcast_id)
    assert broadcast is not None
    assert broadcast["source_chat_id"] == 42
    assert broadcast["source_message_id"] == 1

    await fake_telegram_server.push_callback_query(
        data=_admin_callback(AdminAction.delete_broadcast, broadcast_id),
        message_id=103,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Удалить рассылку" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_callback_query(
        data=_admin_callback(AdminAction.confirm_delete, broadcast_id),
        message_id=104,
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Рассылка удалена.",
    )
    broadcast = await read_admin_broadcast(broadcast_id)
    assert broadcast is not None
    assert broadcast["status"] == "deleted"
    assert broadcast["deleted_at"] is not None


async def test_private_message_reactivates_user_without_resetting_timezone(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    create_functional_user: Callable[..., Awaitable[None]],
    read_user_state: Callable[[int], Awaitable[tuple[int, bool] | None]],
) -> None:
    await create_functional_user(
        user_id=700,
        timezone_offset_minutes=-300,
        is_active=False,
    )

    await fake_telegram_server.push_message(text="/start", user_id=700)
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("chat_id") == 700,
    )

    assert await read_user_state(700) == (-300, True)


def _admin_callback(action: AdminAction, broadcast_id: int = 0) -> str:
    return AdminCallbackData(action=action, broadcast_id=broadcast_id).pack()


def _reply_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["keyboard"] for button in row]


def _inline_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]

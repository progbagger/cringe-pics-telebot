from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import time
from typing import Any

import pytest
from hamcrest import assert_that, contains_string, equal_to, has_item

from tests.functional.conftest import (
    FakeTelegramServer,
    FunctionalSubscriptionFolder,
    FunctionalSubscriptionType,
)

ADMIN_FOLDER_CATEGORIES = (
    FunctionalSubscriptionType(1, "/active", time(8), "active"),
    FunctionalSubscriptionType(2, "/inactive", time(9), "inactive", is_active=False),
    FunctionalSubscriptionType(3, "/instant", None, "instant"),
    FunctionalSubscriptionType(4, "/other", time(10), "other"),
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(ADMIN_FOLDER_CATEGORIES)


async def test_admin_creates_renames_and_rejects_duplicate_folder_name(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    seed_functional_subscription_folders: Callable[[tuple[FunctionalSubscriptionFolder, ...]], Awaitable[None]],
    read_functional_subscription_folders: Callable[[], Awaitable[tuple[FunctionalSubscriptionFolder, ...]]],
) -> None:
    await set_functional_administrator(user_id=42)
    await seed_functional_subscription_folders((FunctionalSubscriptionFolder(10, "Сезоны", ()),))

    panel = await _open_admin_panel(fake_telegram_server)
    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(panel["payload"], "Управление папками") or "",
    )
    folder_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Управление папками" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_texts(folder_list["payload"]),
        equal_to(["📁 Сезоны", "Создать папку", "Назад"]),
    )

    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(folder_list["payload"], "Создать папку") or "",
        message_id=101,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Создание папки" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="   ")
    invalid_name = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Название папки не должно быть пустым" in request["payload"].get("text", ""),
    )
    assert_that(_inline_keyboard_button_texts(invalid_name["payload"]), equal_to(["Отмена"]))

    await fake_telegram_server.push_message(text=" Праздники ")
    created = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("text", "").startswith("Папка создана."),
    )
    assert_that(created["payload"]["text"], contains_string("Папка «Праздники»"))
    assert_that(_inline_keyboard_button_texts(created["payload"]), has_item("❌ /inactive"))
    assert_that(_inline_keyboard_button_texts(created["payload"]), has_item("❌ /instant"))

    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(created["payload"], "Переименовать") or "",
        message_id=102,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Переименование папки «Праздники»" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="Сезоны")
    duplicate_name = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Папка с таким названием уже существует" in request["payload"].get("text", ""),
    )
    assert_that(_inline_keyboard_button_texts(duplicate_name["payload"]), equal_to(["Отмена"]))

    await fake_telegram_server.push_message(text="Выходные")
    renamed = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("text", "").startswith("Папка переименована."),
    )
    assert_that(renamed["payload"]["text"], contains_string("Папка «Выходные»"))
    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(renamed["payload"], "Назад") or "",
        message_id=103,
    )
    returned_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 103 and "Управление папками" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        _inline_keyboard_button_texts(returned_list["payload"]),
        equal_to(["📁 Выходные", "📁 Сезоны", "Создать папку", "Назад"]),
    )
    assert_that(
        await read_functional_subscription_folders(),
        equal_to(
            (
                FunctionalSubscriptionFolder(10, "Сезоны", ()),
                FunctionalSubscriptionFolder(11, "Выходные", ()),
            )
        ),
    )


async def test_admin_edits_membership_and_deletes_folder_without_touching_categories_or_subscriptions(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    seed_functional_subscription_folders: Callable[[tuple[FunctionalSubscriptionFolder, ...]], Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    read_user_subscription_type_ids: Callable[[int], Awaitable[tuple[int, ...]]],
    read_functional_subscription_folders: Callable[[], Awaitable[tuple[FunctionalSubscriptionFolder, ...]]],
    count_functional_subscription_types: Callable[[], Awaitable[int]],
) -> None:
    await set_functional_administrator(user_id=42)
    await seed_functional_subscription_folders(
        (
            FunctionalSubscriptionFolder(10, "Праздники", (1,)),
            FunctionalSubscriptionFolder(11, "Другая", (4,)),
        )
    )
    await create_user_subscription(user_id=99, subscription_type_id=1)

    panel = await _open_admin_panel(fake_telegram_server)
    folder_list = await _open_folder_list(fake_telegram_server, panel)
    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(folder_list["payload"], "📁 Праздники") or "",
        message_id=101,
    )
    editor = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 101 and "Папка «Праздники»" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        _inline_keyboard_button_texts(editor["payload"]),
        equal_to(
            [
                "✅ /active",
                "❌ /inactive",
                "❌ /instant",
                "📁 /other — Другая",
                "Переименовать",
                "Удалить папку",
                "Назад",
            ]
        ),
    )

    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(editor["payload"], "❌ /inactive") or "",
        message_id=102,
    )
    added = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 102
            and "✅ /inactive" in _inline_keyboard_button_texts(request["payload"])
        ),
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Категория добавлена в папку.",
    )

    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(added["payload"], "✅ /active") or "",
        message_id=103,
    )
    removed = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 103
            and "❌ /active" in _inline_keyboard_button_texts(request["payload"])
        ),
    )
    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(removed["payload"], "📁 /other — Другая") or "",
        message_id=104,
    )
    conflict = await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Сначала удалите категорию из папки «Другая».",
    )
    assert_that(conflict["payload"]["show_alert"], equal_to(True))
    refreshed = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: request["payload"].get("message_id") == 104,
    )

    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(refreshed["payload"], "Удалить папку") or "",
        message_id=105,
    )
    confirmation = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 105
            and "Удалить папку «Праздники»?" in request["payload"].get("text", "")
        ),
    )
    assert_that(confirmation["payload"]["text"], contains_string("Категорий в папке: <b>1</b>"))
    assert_that(confirmation["payload"]["text"], contains_string("подписки и расписания сохранятся"))

    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(confirmation["payload"], "Удалить") or "",
        message_id=106,
    )
    deleted = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 106
            and "Папка «Праздники» удалена" in request["payload"].get("text", "")
        ),
    )
    assert "📁 Праздники" not in _inline_keyboard_button_texts(deleted["payload"])
    assert_that(await read_user_subscription_type_ids(99), equal_to((1,)))
    assert_that(await count_functional_subscription_types(), equal_to(4))
    assert_that(
        await read_functional_subscription_folders(),
        equal_to((FunctionalSubscriptionFolder(11, "Другая", (4,)),)),
    )

    await fake_telegram_server.push_message(text="/subscriptions", user_id=99)
    subscriptions = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: (
            request["payload"].get("chat_id") == 99 and "список" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        _inline_keyboard_button_texts(subscriptions["payload"]),
        equal_to(["📁 Другая", "✅ /active – 08:00 · ежедневно"]),
    )


async def test_admin_folder_callback_rejects_deleted_folder(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    seed_functional_subscription_folders: Callable[[tuple[FunctionalSubscriptionFolder, ...]], Awaitable[None]],
    delete_functional_subscription_folder: Callable[[int], Awaitable[None]],
) -> None:
    await set_functional_administrator(user_id=42)
    await seed_functional_subscription_folders((FunctionalSubscriptionFolder(10, "Праздники", (1,)),))

    panel = await _open_admin_panel(fake_telegram_server)
    folder_list = await _open_folder_list(fake_telegram_server, panel)
    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(folder_list["payload"], "📁 Праздники") or "",
        message_id=101,
    )
    editor = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: request["payload"].get("message_id") == 101,
    )
    stale_callback = _button_callback_data(editor["payload"], "❌ /inactive") or ""

    await delete_functional_subscription_folder(10)
    await fake_telegram_server.push_callback_query(data=stale_callback, message_id=102)
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Папка больше недоступна.",
    )
    refreshed_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == 102 and "Управление папками" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        _inline_keyboard_button_texts(refreshed_list["payload"]),
        equal_to(["Создать папку", "Назад"]),
    )


async def _open_admin_panel(fake_telegram_server: FakeTelegramServer) -> dict[str, Any]:
    await fake_telegram_server.push_message(text="/admin")
    return await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("text") == "<b>Админ-панель</b>\n\nВыберите действие.",
    )


async def _open_folder_list(
    fake_telegram_server: FakeTelegramServer,
    panel: dict[str, Any],
) -> dict[str, Any]:
    await fake_telegram_server.push_callback_query(
        data=_button_callback_data(panel["payload"], "Управление папками") or "",
    )
    return await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Управление папками" in request["payload"].get("text", ""),
    )


def _button_callback_data(payload: dict[str, Any], text: str) -> str | None:
    for row in payload["reply_markup"]["inline_keyboard"]:
        for button in row:
            if button["text"] == text:
                return button["callback_data"]
    return None


def _inline_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]

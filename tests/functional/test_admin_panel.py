from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import pytest
from hamcrest import (
    assert_that,
    contains_string,
    empty,
    equal_to,
    greater_than,
    has_entries,
    has_item,
    has_length,
    none,
)

from cringe_pics_telebot.bot.admin_broadcast_callback_data import (
    AdminBroadcastAction,
    AdminBroadcastCallbackData,
)
from cringe_pics_telebot.bot.admin_category_callback_data import (
    AdminCategoryAction,
    AdminCategoryCallbackData,
)
from cringe_pics_telebot.services.media_sync import MediaSyncSummary
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
    assert_that(_reply_keyboard_button_texts(start_request["payload"])[0], equal_to("Админ-панель"))

    await fake_telegram_server.push_message(text="/admin")
    admin_panel = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Админ-панель" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_texts(admin_panel["payload"]),
        equal_to(["Рассылки", "Управление категориями"]),
    )

    await set_functional_administrator(user_id=42, enabled=False)
    await fake_telegram_server.reset()
    await fake_telegram_server.push_message(text="/admin")
    non_admin_request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Что умеет бот" in request["payload"].get("text", ""),
    )
    button_texts = _reply_keyboard_button_texts(non_admin_request["payload"])
    assert_that([text for text in button_texts if text == "Админ-панель"], empty())

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
        data=_admin_broadcast_callback(AdminBroadcastAction.new_broadcast),
        user_id=999,
    )
    await fake_telegram_server.push_callback_query(
        data=_admin_category_callback(AdminCategoryAction.categories),
        user_id=999,
        message_id=101,
    )
    await fake_telegram_server.push_message(text="synchronization", user_id=999)
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("chat_id") == 999,
    )

    assert_that(await fake_telegram_server.requests(method="editMessageText"), empty())
    assert_that(await fake_telegram_server.requests(method="answerCallbackQuery"), empty())


async def test_admin_manages_category_aliases_used_by_inline_search(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_category_aliases: Callable[[int], Awaitable[tuple[str, ...] | None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await synchronize_functional_media_catalog()
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request("sendMessage")

    await fake_telegram_server.push_callback_query(
        data=_admin_category_callback(AdminCategoryAction.categories),
    )
    category_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Управление категориями" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_texts(category_list["payload"]),
        equal_to(["/day", "/evening", "/morning", "/night", "/random", "Назад"]),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_category_callback(AdminCategoryAction.category, category_id=2),
        message_id=101,
    )
    category_details = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Категория /day" in request["payload"].get("text", ""),
    )
    assert_that(category_details["payload"]["text"], contains_string("<code>день</code>"))
    assert_that(
        _inline_keyboard_button_texts(category_details["payload"]),
        equal_to(["Изменить алиасы", "Очистить алиасы", "Назад"]),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_category_callback(AdminCategoryAction.edit_aliases, category_id=2),
        message_id=102,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "новый полный список" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text=" \n / \n")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Не найдено ни одного" in request["payload"].get("text", ""),
    )

    await fake_telegram_server.push_message(text="  полдень  \n/ДЕНЬ\nдень\n\nс обеда")
    updated_details = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Алиасы категории обновлены" in request["payload"].get("text", ""),
    )
    assert_that(updated_details["payload"]["text"], contains_string("<code>полдень</code>"))
    assert_that(await read_category_aliases(2), equal_to(("полдень", "/ДЕНЬ", "с обеда")))

    await fake_telegram_server.push_inline_query(query="  ПОЛД  ", query_id="inline-admin-alias")
    inline_answer = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-admin-alias",
    )
    assert_that(len(inline_answer["payload"]["results"]), greater_than(0))
    assert_that(
        {result["description"] for result in inline_answer["payload"]["results"]},
        equal_to({"Категория /day"}),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_category_callback(AdminCategoryAction.clear_aliases, category_id=2),
        message_id=103,
    )
    cleared_details = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<i>не заданы</i>" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_texts(cleared_details["payload"]),
        equal_to(["Изменить алиасы", "Назад"]),
    )
    assert_that(await read_category_aliases(2), equal_to(()))


async def test_empty_broadcast_list_starts_creation_and_validates_schedule(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_admin_broadcasts: Callable[[], Awaitable[list[dict[str, Any]]]],
    read_admin_broadcast_recipient_ids: Callable[[int], Awaitable[list[int]]],
    read_user_state: Callable[[int], Awaitable[tuple[int, bool] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Админ-панель" in request["payload"].get("text", ""),
    )

    await fake_telegram_server.push_callback_query(data=_admin_broadcast_callback(AdminBroadcastAction.broadcasts))
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
    assert_that(await read_admin_broadcasts(), empty())

    await fake_telegram_server.push_message(text="20.08.2099 10:00")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "дополнительные Telegram user ID" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="700, 800 700")
    confirmation = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Рассылка запланирована" in request["payload"].get("text", ""),
    )

    broadcasts = await read_admin_broadcasts()
    assert_that(broadcasts, has_length(1))
    assert_that(
        broadcasts[0],
        has_entries(
            created_by_user_id=42,
            source_chat_id=42,
            source_message_id=1,
            scheduled_local_at=datetime(2099, 8, 20, 10, 0),
            timezone_offset_minutes=none(),
            status="scheduled",
        ),
    )
    assert_that(await read_admin_broadcast_recipient_ids(broadcasts[0]["id"]), equal_to([700, 800]))
    assert_that(await read_user_state(700), equal_to((420, False)))
    assert_that(await read_user_state(800), equal_to((420, False)))
    assert_that(_inline_keyboard_button_texts(confirmation["payload"]), has_item("Новая рассылка"))


async def test_admin_edits_and_soft_deletes_existing_broadcast(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    create_functional_admin_broadcast: Callable[..., Awaitable[int]],
    read_admin_broadcast: Callable[[int], Awaitable[dict[str, Any] | None]],
    read_admin_broadcast_recipient_ids: Callable[[int], Awaitable[list[int]]],
) -> None:
    await set_functional_administrator(user_id=42)
    broadcast_id = await create_functional_admin_broadcast(
        scheduled_local_at=datetime(2099, 8, 20, 10, 0),
    )
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request("sendMessage")

    await fake_telegram_server.push_callback_query(data=_admin_broadcast_callback(AdminBroadcastAction.broadcasts))
    broadcast_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Запланированные рассылки" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_texts(broadcast_list["payload"]),
        equal_to(["20.08 10:00 · локально", "✏️", "🗑", "Новая рассылка", "Назад"]),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.edit_broadcast, broadcast_id),
    )
    broadcast_details = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Рассылка #" in request["payload"].get("text", ""),
    )
    assert_that(broadcast_details["payload"]["text"], contains_string("До отправки для вас: <b>"))

    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.edit_schedule, broadcast_id),
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
    assert_that(
        [(item["scheduled_local_at"], item["timezone_offset_minutes"]) for item in [broadcast] if item is not None],
        equal_to([(datetime(2099, 8, 21, 11, 30), 240)]),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.edit_message, broadcast_id),
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
    assert_that(
        [(item["source_chat_id"], item["source_message_id"]) for item in [broadcast] if item is not None],
        equal_to([(42, 1)]),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.edit_recipients, broadcast_id),
        message_id=103,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "новый список дополнительных" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="900 901")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Дополнительные получатели обновлены" in request["payload"].get("text", ""),
    )
    assert_that(await read_admin_broadcast_recipient_ids(broadcast_id), equal_to([900, 901]))

    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.delete_broadcast, broadcast_id),
        message_id=104,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Удалить рассылку" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.confirm_delete, broadcast_id),
        message_id=105,
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Рассылка удалена.",
    )
    broadcast = await read_admin_broadcast(broadcast_id)
    assert_that(
        [(item["status"], item["deleted_at"] is not None) for item in [broadcast] if item is not None],
        equal_to([("deleted", True)]),
    )


async def test_admin_can_skip_explicit_recipients(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_admin_broadcasts: Callable[[], Awaitable[list[dict[str, Any]]]],
    read_admin_broadcast_recipient_ids: Callable[[int], Awaitable[list[int]]],
) -> None:
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request("sendMessage")
    await fake_telegram_server.push_callback_query(data=_admin_broadcast_callback(AdminBroadcastAction.broadcasts))
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Новая рассылка" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="Сообщение без дополнительных ID")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "ДД.ММ.ГГГГ" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_message(text="20.08.2099 10:00")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "дополнительные Telegram user ID" in request["payload"].get("text", ""),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.skip_recipients),
        message_id=106,
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: "Дополнительных получателей" in request["payload"].get("text", ""),
    )

    broadcasts = await read_admin_broadcasts()
    assert_that(broadcasts, has_length(1))
    assert_that(broadcasts[0], has_entries(created_by_user_id=42))
    assert_that(await read_admin_broadcast_recipient_ids(broadcasts[0]["id"]), empty())


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

    assert_that(await read_user_state(700), equal_to((-300, True)))


def _admin_broadcast_callback(action: AdminBroadcastAction, broadcast_id: int = 0) -> str:
    return AdminBroadcastCallbackData(action=action, broadcast_id=broadcast_id).pack()


def _admin_category_callback(action: AdminCategoryAction, category_id: int = 0) -> str:
    return AdminCategoryCallbackData(action=action, category_id=category_id).pack()


def _reply_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["keyboard"] for button in row]


def _inline_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]

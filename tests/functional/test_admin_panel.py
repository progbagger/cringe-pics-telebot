import re
from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta, timezone
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
from cringe_pics_telebot.bot.admin_panel_callback_data import AdminPanelAction, AdminPanelCallbackData
from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.redis import connect as connect_redis
from cringe_pics_telebot.services.admin_broadcast_schedules import parse_admin_broadcast_schedule
from cringe_pics_telebot.services.media_sync import MEDIA_SYNC_LEASE_KEY, MediaSyncSummary
from cringe_pics_telebot.services.timezones import DEFAULT_TIMEZONE_OFFSET_MINUTES
from tests.functional.conftest import (
    REDIS_ENV,
    SEEDED_SUBSCRIPTION_TYPES,
    DependencyPorts,
    FakeTelegramServer,
    FakeYandexServer,
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
        equal_to(["Уведомления", "Управление категориями", "Синхронизировать медиа"]),
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
    fake_yandex_server: FakeYandexServer,
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
    await fake_telegram_server.push_callback_query(
        data=_admin_panel_callback(AdminPanelAction.synchronize_media),
        user_id=999,
        message_id=102,
    )
    await fake_telegram_server.push_message(text="synchronization", user_id=999)
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("chat_id") == 999,
    )

    assert_that(await fake_telegram_server.requests(method="editMessageText"), empty())
    assert_that(await fake_telegram_server.requests(method="answerCallbackQuery"), empty())
    assert_that(await fake_yandex_server.requests(), empty())


async def test_admin_manually_synchronizes_active_and_inactive_media(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    await reset_functional_state(
        (
            FunctionalSubscriptionType(1, "/day", time(13), "day"),
            FunctionalSubscriptionType(2, "/inactive", time(14), "inactive", is_active=False),
        )
    )
    await fake_yandex_server.configure_directory(
        "day",
        images=[{"name": "first.png"}, {"name": "second.gif", "mime_type": "image/gif"}],
    )
    await fake_yandex_server.configure_directory("inactive", images=[{"name": "inactive.png"}])
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request("sendMessage")
    await fake_telegram_server.block_method("answerCallbackQuery")

    try:
        await fake_telegram_server.push_callback_query(
            data=_admin_panel_callback(AdminPanelAction.synchronize_media),
            message_id=110,
        )
        await fake_telegram_server.wait_for_request(
            "answerCallbackQuery",
            predicate=lambda request: request["payload"].get("callback_query_id") == "callback-110",
        )
        assert_that(await fake_yandex_server.requests(), empty())
    finally:
        await fake_telegram_server.release_method("answerCallbackQuery")

    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Синхронизация медиа началась" in request["payload"].get("text", ""),
    )
    result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<b>Синхронизация медиа завершена</b>" in request["payload"].get("text", ""),
    )

    for expected_line in (
        "Обработано категорий: <b>2</b>",
        "Категорий с ошибками: <b>0</b>",
        "Найдено медиа: <b>3</b>",
        "Создано записей: <b>3</b>",
        "Изменено записей: <b>0</b>",
        "Повторно активировано медиа: <b>0</b>",
        "Деактивировано отсутствующее медиа: <b>0</b>",
    ):
        assert_that(result["payload"]["text"], contains_string(expected_line))
    assert_that(
        _inline_keyboard_button_texts(result["payload"]),
        equal_to(["Синхронизировать повторно", "Назад"]),
    )
    assert_that(
        [
            request["params"]["path"]
            for request in await fake_yandex_server.requests()
            if request["method"] == "resources"
        ],
        equal_to(["app:/day", "app:/inactive"]),
    )
    assert_that(
        {request["method"] for request in await fake_yandex_server.requests()} & {"resources/download", "download"},
        empty(),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_panel_callback(AdminPanelAction.panel),
        message_id=110,
    )
    returned_panel = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: request["payload"].get("text") == "<b>Админ-панель</b>\n\nВыберите действие.",
    )
    assert_that(
        _inline_keyboard_button_texts(returned_panel["payload"]),
        equal_to(["Уведомления", "Управление категориями", "Синхронизировать медиа"]),
    )


async def test_admin_media_sync_reports_occupied_lease_without_starting_yandex_requests(
    bot_process: subprocess.Process,
    docker_compose: DependencyPorts,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    await set_functional_administrator(user_id=42)
    async with connect_redis(
        username=REDIS_ENV["REDIS_USERNAME"],
        password=REDIS_ENV["REDIS_PASSWORD"],
        port=docker_compose.redis,
        host=REDIS_ENV["REDIS_HOST"],
    ):
        await cache.set(
            key=MEDIA_SYNC_LEASE_KEY,
            value="another-instance",
            cls=str,
            ttl=timedelta(minutes=1),
        )

        await fake_telegram_server.push_callback_query(
            data=_admin_panel_callback(AdminPanelAction.synchronize_media),
            message_id=120,
        )
        await fake_telegram_server.wait_for_request(
            "answerCallbackQuery",
            predicate=lambda request: request["payload"].get("callback_query_id") == "callback-120",
        )
        result = await fake_telegram_server.wait_for_request(
            "editMessageText",
            predicate=lambda request: "уже выполняется" in request["payload"].get("text", ""),
        )

        assert_that(await cache.get(key=MEDIA_SYNC_LEASE_KEY, cls=str), equal_to("another-instance"))

    assert_that(result["payload"]["text"], contains_string("Новый запуск не начат"))
    assert_that(
        _inline_keyboard_button_texts(result["payload"]),
        equal_to(["Синхронизировать повторно", "Назад"]),
    )
    assert_that(await fake_yandex_server.requests(), empty())


async def test_admin_media_sync_shows_partial_result_and_allows_retry(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    await reset_functional_state(
        (
            FunctionalSubscriptionType(1, "/day", time(13), "day"),
            FunctionalSubscriptionType(2, "/broken", time(14), "broken", is_active=False),
        )
    )
    await fake_yandex_server.configure_directory("day", images=[{"name": "day.png"}])
    await fake_yandex_server.configure_directory("broken", fail=True)
    await set_functional_administrator(user_id=42)

    await fake_telegram_server.push_callback_query(
        data=_admin_panel_callback(AdminPanelAction.synchronize_media),
        message_id=130,
    )
    partial_result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "завершена частично" in request["payload"].get("text", ""),
    )
    for expected_line in (
        "Обработано категорий: <b>1</b>",
        "Категорий с ошибками: <b>1</b>",
        "Найдено медиа: <b>1</b>",
        "Создано записей: <b>1</b>",
        "Изменено записей: <b>0</b>",
        "Повторно активировано медиа: <b>0</b>",
        "Деактивировано отсутствующее медиа: <b>0</b>",
    ):
        assert_that(partial_result["payload"]["text"], contains_string(expected_line))

    await fake_yandex_server.configure_directory("broken", images=[{"name": "recovered.png"}])
    await fake_telegram_server.push_callback_query(
        data=_admin_panel_callback(AdminPanelAction.synchronize_media),
        message_id=131,
    )
    retried_result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<b>Синхронизация медиа завершена</b>" in request["payload"].get("text", ""),
    )
    assert_that(retried_result["payload"]["text"], contains_string("Обработано категорий: <b>2</b>"))
    assert_that(retried_result["payload"]["text"], contains_string("Категорий с ошибками: <b>0</b>"))
    assert_that(retried_result["payload"]["text"], contains_string("Создано записей: <b>1</b>"))


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
        equal_to(
            [
                "✅ /day — активна",
                "✅ /evening — активна",
                "✅ /morning — активна",
                "✅ /night — активна",
                "✅ /random — активна",
                "Создать категорию",
                "Назад",
            ]
        ),
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
        equal_to(
            [
                "Деактивировать",
                "Изменить время отправки",
                "Отключить расписание",
                "Изменить алиасы",
                "Очистить алиасы",
                "Назад",
            ]
        ),
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
        equal_to(["Деактивировать", "Изменить время отправки", "Отключить расписание", "Изменить алиасы", "Назад"]),
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
        predicate=lambda request: "Новое уведомление" in request["payload"].get("text", ""),
    )

    initial_prompt_started_at = datetime.now(UTC)
    await fake_telegram_server.push_message(text="Важное сообщение")
    initial_prompt = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "ДД.ММ.ГГГГ" in request["payload"].get("text", ""),
    )
    initial_prompt_finished_at = datetime.now(UTC)
    _assert_dynamic_schedule_example(
        initial_prompt["payload"]["text"],
        generated_after=initial_prompt_started_at,
        generated_before=initial_prompt_finished_at,
    )

    retry_prompt_started_at = datetime.now(UTC)
    await fake_telegram_server.push_message(text="не дата")
    retry_prompt = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Не удалось распознать" in request["payload"].get("text", ""),
    )
    retry_prompt_finished_at = datetime.now(UTC)
    _assert_dynamic_schedule_example(
        retry_prompt["payload"]["text"],
        generated_after=retry_prompt_started_at,
        generated_before=retry_prompt_finished_at,
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
        predicate=lambda request: "Уведомление запланировано" in request["payload"].get("text", ""),
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
    assert_that(_inline_keyboard_button_texts(confirmation["payload"]), has_item("Новое уведомление"))


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
        predicate=lambda request: "Запланированные уведомления" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_rows(broadcast_list["payload"]),
        equal_to(
            [
                ["20.08 10:00 · локально"],
                ["✏️", "🗑"],
                ["Новое уведомление"],
                ["Назад"],
            ]
        ),
    )

    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.edit_broadcast, broadcast_id),
    )
    broadcast_details = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Уведомление #" in request["payload"].get("text", ""),
    )
    assert_that(broadcast_details["payload"]["text"], contains_string("До отправки для вас: <b>"))

    edit_prompt_started_at = datetime.now(UTC)
    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.edit_schedule, broadcast_id),
        message_id=101,
    )
    edit_prompt = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Введите новую дату" in request["payload"].get("text", ""),
    )
    edit_prompt_finished_at = datetime.now(UTC)
    _assert_dynamic_schedule_example(
        edit_prompt["payload"]["text"],
        generated_after=edit_prompt_started_at,
        generated_before=edit_prompt_finished_at,
    )
    await fake_telegram_server.push_message(text="21.08.2099 11:30 +04:00")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Дата и время уведомления обновлены" in request["payload"].get("text", ""),
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
        predicate=lambda request: "Сообщение уведомления обновлено" in request["payload"].get("text", ""),
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
        predicate=lambda request: "Удалить уведомление" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.push_callback_query(
        data=_admin_broadcast_callback(AdminBroadcastAction.confirm_delete, broadcast_id),
        message_id=105,
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Уведомление удалено.",
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
        predicate=lambda request: "Новое уведомление" in request["payload"].get("text", ""),
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
    alert = await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: "Дополнительных получателей" in request["payload"].get("text", ""),
    )
    alert_text = alert["payload"]["text"]
    assert alert["payload"]["show_alert"] is True
    assert "<" not in alert_text
    assert ">" not in alert_text
    assert_that(alert_text, contains_string("20.08.2099 10:00 — локальное время каждого получателя"))
    assert_that(alert_text, contains_string("Дополнительных получателей: 0."))

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


def _assert_dynamic_schedule_example(
    prompt: str,
    *,
    generated_after: datetime,
    generated_before: datetime,
) -> None:
    match = re.search(r"Например: <code>(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2} \+07:00)</code>", prompt)
    assert match is not None
    assert "20.08.2026" not in prompt

    example = parse_admin_broadcast_schedule(match.group(1), now=generated_after)
    assert example.timezone_offset_minutes == DEFAULT_TIMEZONE_OFFSET_MINUTES

    example_timezone = timezone(timedelta(minutes=DEFAULT_TIMEZONE_OFFSET_MINUTES))
    earliest = generated_after.astimezone(example_timezone).replace(tzinfo=None, second=0, microsecond=0) + timedelta(
        hours=1
    )
    latest = generated_before.astimezone(example_timezone).replace(tzinfo=None, second=0, microsecond=0) + timedelta(
        hours=1
    )
    assert earliest <= example.local_at <= latest


def _admin_panel_callback(action: AdminPanelAction) -> str:
    return AdminPanelCallbackData(action=action).pack()


def _admin_broadcast_callback(action: AdminBroadcastAction, broadcast_id: int = 0) -> str:
    return AdminBroadcastCallbackData(action=action, broadcast_id=broadcast_id).pack()


def _admin_category_callback(action: AdminCategoryAction, category_id: int = 0) -> str:
    return AdminCategoryCallbackData(action=action, category_id=category_id).pack()


def _reply_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["keyboard"] for button in row]


def _inline_keyboard_button_rows(payload: dict[str, Any]) -> list[list[str]]:
    return [[button["text"] for button in row] for row in payload["reply_markup"]["inline_keyboard"]]


def _inline_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button_text for row in _inline_keyboard_button_rows(payload) for button_text in row]

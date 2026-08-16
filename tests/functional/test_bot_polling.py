from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import time
from typing import Any

import pytest

from cringe_pics_telebot.bot.subscription_callback_data import SubscriptionCallbackData
from tests.functional.conftest import (
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(())


@pytest.mark.parametrize("text", ["/start", "/help", "непонятное сообщение"])
async def test_bot_shows_start_screen_for_entry_points(
    text: str,
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
) -> None:
    await fake_telegram_server.push_message(text=text, first_name="Danil")

    request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=_is_start_answer,
    )

    payload = request["payload"]
    assert payload["chat_id"] == 42
    assert "Что умеет бот" in payload["text"]
    assert "Danil" in payload["text"]
    assert (
        "<code>/random</code>, <code>/morning</code>, <code>/day</code>, <code>/evening</code>, <code>/night</code>"
    ) in payload["text"]
    assert "<code>/list</code> или <code>/subscriptions</code>" in payload["text"]
    assert "<code>/timezone [+HH:MM]</code>" in payload["text"]
    assert "UTC+07:00" in payload["text"]
    assert "<code>@имя_бота</code>" in payload["text"]
    assert "Первый результат 🎲" in payload["text"]
    assert _reply_keyboard_button_texts(payload) == [
        "Подписки",
        "/random",
        "/morning",
        "/day",
        "/evening",
        "/night",
    ]


@pytest.mark.parametrize("text", ["/list", "/subscriptions", "Подписки"])
async def test_bot_shows_subscription_list(
    text: str,
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
) -> None:
    await fake_telegram_server.push_message(text=text)

    request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=_is_subscription_list_answer,
    )

    payload = request["payload"]
    assert payload["chat_id"] == 42
    assert "список" in payload["text"]
    assert "UTC+07:00" in payload["text"]
    assert _inline_keyboard_button_texts(payload) == [
        "❌ /random – 00:00",
        "❌ /morning – 08:00",
        "❌ /day – 13:00",
        "❌ /evening – 19:00",
        "❌ /night – 23:00",
    ]


async def test_bot_shows_default_timezone(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
) -> None:
    await fake_telegram_server.push_message(text="/timezone")

    request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "текущий часовой пояс" in request["payload"].get("text", ""),
    )

    assert request["payload"]["chat_id"] == 42
    assert "UTC+07:00" in request["payload"]["text"]


async def test_bot_saves_timezone(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    read_user_timezone_offset: Callable[[int], Awaitable[int | None]],
) -> None:
    await fake_telegram_server.push_message(text="/timezone +04:00")

    request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Часовой пояс сохранён" in request["payload"].get("text", ""),
    )

    assert "UTC+04:00" in request["payload"]["text"]
    assert await read_user_timezone_offset(42) == 240


async def test_bot_rejects_invalid_timezone_without_changing_saved_value(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    read_user_timezone_offset: Callable[[int], Awaitable[int | None]],
) -> None:
    await fake_telegram_server.push_message(text="/timezone -05:30")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Часовой пояс сохранён" in request["payload"].get("text", ""),
    )

    await fake_telegram_server.push_message(text="/timezone +14:30")
    request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Не удалось распознать" in request["payload"].get("text", ""),
    )

    assert "-12:00" in request["payload"]["text"]
    assert "+14:00" in request["payload"]["text"]
    assert await read_user_timezone_offset(42) == -330


async def test_bot_subscribes_from_callback(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
) -> None:
    await fake_telegram_server.push_callback_query(data=_subscription_callback(category_id=1, subscribe=True))

    subscribe_answer = await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Подписка оформлена!",
    )
    assert subscribe_answer["payload"]["callback_query_id"] == "callback-100"

    subscribe_markup = await fake_telegram_server.wait_for_request(
        "editMessageReplyMarkup",
        predicate=lambda request: (
            _button_callback_data(request["payload"], "/morning")
            == _subscription_callback(
                category_id=1,
                subscribe=False,
            )
        ),
    )
    assert "✅ /morning – 08:00" in _inline_keyboard_button_texts(subscribe_markup["payload"])


async def test_bot_unsubscribes_from_callback(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    user_subscribed_to_morning: None,
) -> None:
    await fake_telegram_server.push_callback_query(
        data=_subscription_callback(category_id=1, subscribe=False),
    )

    unsubscribe_answer = await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Подписка удалена!",
    )
    assert unsubscribe_answer["payload"]["callback_query_id"] == "callback-100"

    unsubscribe_markup = await fake_telegram_server.wait_for_request(
        "editMessageReplyMarkup",
        predicate=lambda request: (
            _button_callback_data(request["payload"], "/morning")
            == _subscription_callback(
                category_id=1,
                subscribe=True,
            )
        ),
    )
    assert "❌ /morning – 08:00" in _inline_keyboard_button_texts(unsubscribe_markup["payload"])


@pytest.mark.parametrize("category_name", ["/morning", "/day", "/evening", "/night", "/random"])
async def test_bot_sends_image_for_subscription_category(
    category_name: str,
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
) -> None:
    await fake_telegram_server.push_message(text=category_name)

    choosing_message = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("text") == "<i>Выбираю картинку</i>",
    )
    assert _reply_to_message_id(choosing_message["payload"]) == 1

    edit_media = await fake_telegram_server.wait_for_request("editMessageMedia")
    assert edit_media["payload"]["chat_id"] == 42
    assert edit_media["payload"]["media"]["type"] == "photo"
    assert edit_media["payload"]["media"]["media"].startswith("attach://")

    yandex_requests = await fake_yandex_server.requests()
    expected_list_request = {
        "method": "resources",
        "params": {"path": f"app:/{category_name.removeprefix('/')}", "limit": "1000", "offset": "0"},
    }
    assert expected_list_request in yandex_requests
    assert any(request["method"] == "resources/download" for request in yandex_requests)
    assert any(request["method"] == "download" for request in yandex_requests)


async def test_bot_returns_day_images_for_partial_inline_query(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
) -> None:
    await fake_telegram_server.push_inline_query(query="  dA ")

    request = await fake_telegram_server.wait_for_request("answerInlineQuery")

    payload = request["payload"]
    assert payload["inline_query_id"] == "inline-100"
    assert payload["cache_time"] == 0
    assert payload["is_personal"] is True
    assert len(payload["results"]) == 2
    random_result, ordinary_result = payload["results"]
    assert random_result["type"] == ordinary_result["type"] == "photo"
    assert random_result["title"] == "🎲 Выбрать случайную картинку"
    assert ordinary_result["title"] in {"image.png", "second.png"}
    assert random_result["description"] == ordinary_result["description"] == "Категория /day"
    assert random_result["thumbnail_url"] == random_result["photo_url"]
    assert ordinary_result["thumbnail_url"] == ordinary_result["photo_url"]
    assert {random_result["photo_url"], ordinary_result["photo_url"]} == {
        f"{fake_yandex_server.base_url}/download/image.png",
        f"{fake_yandex_server.base_url}/download/second.png",
    }
    assert len({random_result["id"], ordinary_result["id"]}) == 2
    assert all(len(result["id"]) == 64 for result in payload["results"])

    yandex_requests = await fake_yandex_server.requests()
    assert {
        "method": "resources",
        "params": {"path": "app:/day", "limit": "1000", "offset": "0"},
    } in yandex_requests
    assert {
        "method": "resources/download",
        "params": {"path": "app:/day/image.png", "fields": "href"},
    } in yandex_requests
    assert {
        "method": "resources/download",
        "params": {"path": "app:/day/second.png", "fields": "href"},
    } in yandex_requests
    assert not any(request["method"] == "download" for request in yandex_requests)


async def test_bot_returns_empty_inline_results_for_unknown_category_and_keeps_polling(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
) -> None:
    await fake_telegram_server.push_inline_query(query="unknown", query_id="inline-unknown")

    request = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-unknown",
    )
    assert request["payload"]["results"] == []

    await fake_telegram_server.push_message(text="still running")
    await fake_telegram_server.wait_for_request("sendMessage", predicate=_is_start_answer)


async def test_bot_returns_empty_inline_results_for_known_empty_category_and_keeps_polling(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/empty", time(0), "empty"),))
    await fake_telegram_server.push_inline_query(query="empty", query_id="inline-empty")

    request = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-empty",
    )
    assert request["payload"]["results"] == []

    await fake_telegram_server.push_message(text="still running after empty inline category")
    await fake_telegram_server.wait_for_request("sendMessage", predicate=_is_start_answer)


async def test_bot_returns_empty_inline_results_when_image_url_fails_and_keeps_polling(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/broken", time(0), "broken"),))
    await fake_telegram_server.push_inline_query(query="broken", query_id="inline-broken")

    request = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-broken",
    )
    assert request["payload"]["results"] == []

    await fake_telegram_server.push_message(text="still running after Yandex failure")
    await fake_telegram_server.wait_for_request("sendMessage", predicate=_is_start_answer)


def _is_start_answer(request: dict[str, Any]) -> bool:
    payload = request["payload"]
    return payload.get("chat_id") == 42 and "Что умеет бот" in payload.get("text", "")


def _is_subscription_list_answer(request: dict[str, Any]) -> bool:
    payload = request["payload"]
    return payload.get("chat_id") == 42 and "список" in payload.get("text", "")


def _subscription_callback(*, category_id: int, subscribe: bool) -> str:
    return SubscriptionCallbackData(category_id=category_id, subscribe=subscribe).pack()


def _reply_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["keyboard"] for button in row]


def _inline_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]


def _button_callback_data(payload: dict[str, Any], text: str) -> str | None:
    for row in payload["reply_markup"]["inline_keyboard"]:
        for button in row:
            if text in button["text"]:
                return button["callback_data"]

    return None


def _reply_to_message_id(payload: dict[str, Any]) -> int | None:
    if "reply_to_message_id" in payload:
        return int(payload["reply_to_message_id"])

    if "reply_parameters" in payload:
        return int(payload["reply_parameters"]["message_id"])

    return None

from asyncio import subprocess
from typing import Any

import pytest

from cringe_pics_telebot.bot.subscription_callback_data import SubscriptionCallbackData
from tests.functional.conftest import (
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)


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
    assert "Приветствую" in payload["text"]
    assert "Danil" in payload["text"]
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
    assert _inline_keyboard_button_texts(payload) == [
        "❌ /random – 00:00",
        "❌ /morning – 08:00",
        "❌ /day – 13:00",
        "❌ /evening – 19:00",
        "❌ /night – 23:00",
    ]


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


def _is_start_answer(request: dict[str, Any]) -> bool:
    payload = request["payload"]
    return payload.get("chat_id") == 42 and "Приветствую" in payload.get("text", "")


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

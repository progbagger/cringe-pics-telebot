from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import time
from typing import Any

import pytest
from hamcrest import (
    assert_that,
    contains_string,
    empty,
    equal_to,
    has_entries,
    has_item,
    has_length,
    is_,
    is_in,
    only_contains,
    starts_with,
)

from cringe_pics_telebot.bot.subscription_callback_data import SubscriptionCallbackData
from cringe_pics_telebot.services.media_sync import MediaSyncSummary
from tests.functional.conftest import (
    FakeStatsDServer,
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
    assert_that(payload["chat_id"], equal_to(42))
    assert_that(payload["text"], contains_string("Что умеет бот"))
    assert_that(payload["text"], contains_string("Danil"))
    assert_that(
        payload["text"],
        contains_string(
            "<code>/random</code>, <code>/morning</code>, <code>/day</code>, <code>/evening</code>, <code>/night</code>"
        ),
    )
    assert_that(payload["text"], contains_string("<code>/list</code> или <code>/subscriptions</code>"))
    assert_that(payload["text"], contains_string("<code>/timezone [+HH:MM]</code>"))
    assert_that(payload["text"], contains_string("UTC+07:00"))
    assert_that(payload["text"], contains_string("<code>@имя_бота</code>"))
    assert_that(payload["text"], contains_string("Первый результат 🎲"))
    assert_that(
        _reply_keyboard_button_texts(payload),
        equal_to(["Подписки", "/random", "/morning", "/day", "/evening", "/night"]),
    )


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
    assert_that(payload["chat_id"], equal_to(42))
    assert_that(payload["text"], contains_string("список"))
    assert_that(payload["text"], contains_string("UTC+07:00"))
    assert_that(
        _inline_keyboard_button_texts(payload),
        equal_to(
            [
                "❌ /random – 00:00",
                "❌ /morning – 08:00",
                "❌ /day – 13:00",
                "❌ /evening – 19:00",
                "❌ /night – 23:00",
            ]
        ),
    )


async def test_bot_shows_default_timezone(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
) -> None:
    await fake_telegram_server.push_message(text="/timezone")

    request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "текущий часовой пояс" in request["payload"].get("text", ""),
    )

    assert_that(request["payload"]["chat_id"], equal_to(42))
    assert_that(request["payload"]["text"], contains_string("UTC+07:00"))


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

    assert_that(request["payload"]["text"], contains_string("UTC+04:00"))
    assert_that(await read_user_timezone_offset(42), equal_to(240))


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

    assert_that(request["payload"]["text"], contains_string("-12:00"))
    assert_that(request["payload"]["text"], contains_string("+14:00"))
    assert_that(await read_user_timezone_offset(42), equal_to(-330))


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
    assert_that(subscribe_answer["payload"]["callback_query_id"], equal_to("callback-100"))

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
    assert_that(_inline_keyboard_button_texts(subscribe_markup["payload"]), has_item("✅ /morning – 08:00"))


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
    assert_that(unsubscribe_answer["payload"]["callback_query_id"], equal_to("callback-100"))

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
    assert_that(_inline_keyboard_button_texts(unsubscribe_markup["payload"]), has_item("❌ /morning – 08:00"))


@pytest.mark.parametrize("category_name", ["/morning", "/day", "/evening", "/night", "/random"])
async def test_bot_sends_image_for_subscription_category(
    category_name: str,
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()
    await fake_telegram_server.push_message(text=category_name)

    choosing_message = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: request["payload"].get("text") == "<i>Выбираю картинку</i>",
    )
    assert_that(_reply_to_message_id(choosing_message["payload"]), equal_to(1))

    edit_media = await fake_telegram_server.wait_for_request("editMessageMedia")
    assert_that(edit_media["payload"]["chat_id"], equal_to(42))
    assert_that(edit_media["payload"]["media"]["type"], equal_to("photo"))
    assert_that(edit_media["payload"]["media"]["media"], starts_with(fake_yandex_server.base_url))

    yandex_methods = [request["method"] for request in await fake_yandex_server.requests()]
    assert_that([method for method in yandex_methods if method == "resources"], empty())
    assert_that(yandex_methods, has_item("resources/download"))
    assert_that([method for method in yandex_methods if method == "download"], empty())


async def test_bot_prefers_pending_media_over_ready_for_ordinary_delivery(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
    read_functional_category_media_states: Callable[[], Awaitable[dict[str, tuple[str, str | None]]]],
) -> None:
    await fake_yandex_server.configure_directory(
        "day",
        images=[{"name": "ready.png"}, {"name": "pending.png"}],
    )
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids({"day/ready.png": "functional-ready-file-id"})
    await fake_telegram_server.reset()
    await fake_yandex_server.reset()

    await fake_telegram_server.push_message(text="/day")
    edit_media = await fake_telegram_server.wait_for_request("editMessageMedia")

    assert_that(
        edit_media["payload"]["media"]["media"],
        equal_to(f"{fake_yandex_server.base_url}/download/pending.png"),
    )
    assert_that(
        sum(request["method"] == "resources/download" for request in await fake_yandex_server.requests()),
        equal_to(1),
    )
    states = await read_functional_category_media_states()
    assert_that(
        {path: state for path, state in states.items() if path.startswith("day/")},
        equal_to(
            {
                "day/pending.png": ("ready", "functional-photo-file-id"),
                "day/ready.png": ("ready", "functional-ready-file-id"),
            }
        ),
    )


async def test_bot_recovers_invalid_catalog_file_id_once(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await fake_yandex_server.configure_directory("day", images=[{"name": "image.png"}])
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    await fake_telegram_server.push_message(text="/day")
    first_edit = await fake_telegram_server.wait_for_request("editMessageMedia")
    assert_that(first_edit["payload"]["media"]["media"], starts_with(fake_yandex_server.base_url))

    await fake_telegram_server.reset()
    await fake_telegram_server.set_invalid_file_ids("functional-photo-file-id")
    await fake_yandex_server.reset()
    await fake_telegram_server.push_message(text="/day")
    recovered_edit = await fake_telegram_server.wait_for_request(
        "editMessageMedia",
        predicate=lambda request: str(request["payload"]["media"]["media"]).startswith(fake_yandex_server.base_url),
    )
    assert_that(recovered_edit["payload"]["media"]["media"], starts_with(fake_yandex_server.base_url))

    edits = await fake_telegram_server.requests(method="editMessageMedia")
    assert_that(
        [request["payload"]["media"]["media"] for request in edits],
        equal_to(
            [
                "functional-photo-file-id",
                f"{fake_yandex_server.base_url}/download/image.png",
            ]
        ),
    )
    assert_that(
        sum(request["method"] == "resources/download" for request in await fake_yandex_server.requests()),
        equal_to(1),
    )


@pytest.mark.parametrize("query", ["  dA ", "  ДНЕ "])
async def test_bot_returns_day_images_for_partial_inline_query(
    bot_process: subprocess.Process,
    fake_statsd_server: FakeStatsDServer,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    query: str,
) -> None:
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()
    await fake_telegram_server.push_inline_query(query=query)

    request = await fake_telegram_server.wait_for_request("answerInlineQuery")

    payload = request["payload"]
    assert_that(payload["inline_query_id"], equal_to("inline-100"))
    assert_that(payload["cache_time"], equal_to(0))
    assert_that(payload["is_personal"], is_(True))
    assert_that(payload["results"], has_length(2))
    random_result, ordinary_result = payload["results"]
    assert_that((random_result["type"], ordinary_result["type"]), equal_to(("photo", "photo")))
    assert_that(random_result["title"], equal_to("🎲 Выбрать случайную картинку"))
    assert_that(ordinary_result["title"], is_in(("image.png", "second.png")))
    assert_that(
        (random_result["description"], ordinary_result["description"]),
        equal_to(("Категория /day", "Категория /day")),
    )
    assert_that(
        (random_result["thumbnail_url"], ordinary_result["thumbnail_url"]),
        equal_to((random_result["photo_url"], ordinary_result["photo_url"])),
    )
    assert_that(
        {random_result["photo_url"], ordinary_result["photo_url"]},
        equal_to(
            {
                f"{fake_yandex_server.base_url}/download/image.png",
                f"{fake_yandex_server.base_url}/download/second.png",
            }
        ),
    )
    assert_that({random_result["id"], ordinary_result["id"]}, has_length(2))
    assert_that([len(result["id"]) for result in payload["results"]], only_contains(64))

    yandex_requests = await fake_yandex_server.requests()
    yandex_methods = [request["method"] for request in yandex_requests]
    assert_that([method for method in yandex_methods if method == "resources"], empty())
    assert_that(
        yandex_requests,
        has_item(
            {
                "method": "resources/download",
                "params": {"path": "app:/day/image.png", "fields": "href"},
            }
        ),
    )
    assert_that(
        yandex_requests,
        has_item(
            {
                "method": "resources/download",
                "params": {"path": "app:/day/second.png", "fields": "href"},
            }
        ),
    )
    assert_that([method for method in yandex_methods if method == "download"], empty())

    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.scenarios.pending_only.total",
        "functional.inline.stages.categories.lookup",
        "functional.inline.stages.media.catalog",
        "functional.inline.stages.media.urls",
        "functional.inline.stages.results.prepare",
        "functional.inline.stages.telegram.answer",
        "functional.inline.dependencies.postgres.calls",
        "functional.inline.dependencies.yandex.calls",
        "functional.inline.dependencies.redis.calls",
        "functional.inline.dependencies.telegram.calls",
        "functional.inline.media.catalog_items",
        "functional.inline.results.sent",
    )
    assert_that(
        all(metrics[name]["type"] == "ms" for name in metrics if ".total" in name or ".stages." in name),
        is_(True),
    )
    assert_that(metrics["functional.inline.dependencies.postgres.calls"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.dependencies.redis.calls"]["value"], equal_to(0))
    assert_that(metrics["functional.inline.dependencies.telegram.calls"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.media.catalog_items"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.results.sent"]["value"], equal_to(2))


async def test_bot_returns_empty_inline_results_for_unknown_category_and_keeps_polling(
    bot_process: subprocess.Process,
    fake_statsd_server: FakeStatsDServer,
    fake_telegram_server: FakeTelegramServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
) -> None:
    await fake_telegram_server.push_inline_query(query="unknown", query_id="inline-unknown")

    request = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-unknown",
    )
    assert_that(request["payload"]["results"], empty())

    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.scenarios.unknown_category.total",
        "functional.inline.dependencies.postgres.calls",
        "functional.inline.dependencies.yandex.calls",
        "functional.inline.dependencies.redis.calls",
        "functional.inline.dependencies.telegram.calls",
    )
    assert_that(metrics["functional.inline.dependencies.postgres.calls"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(0))
    assert_that(metrics["functional.inline.dependencies.redis.calls"]["value"], equal_to(0))
    assert_that(metrics["functional.inline.dependencies.telegram.calls"]["value"], equal_to(1))
    assert_that(await fake_statsd_server.metrics(name="functional.inline.stages.media.catalog"), empty())

    await fake_telegram_server.push_message(text="still running")
    await fake_telegram_server.wait_for_request("sendMessage", predicate=_is_start_answer)


async def test_bot_returns_random_media_per_category_for_empty_inline_query(
    bot_process: subprocess.Process,
    fake_statsd_server: FakeStatsDServer,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    subscription_types = (
        FunctionalSubscriptionType(1, "/ready-photo", time(0), "ready-photo"),
        FunctionalSubscriptionType(2, "/pending-gif", time(1), "pending-gif"),
        FunctionalSubscriptionType(3, "/pending-photo", time(2), "pending-photo"),
        FunctionalSubscriptionType(4, "/empty", time(3), "empty"),
        FunctionalSubscriptionType(5, "/broken", time(4), "broken"),
    )
    await seed_functional_subscription_types(subscription_types)
    await fake_yandex_server.configure_directory("ready-photo", images=[{"name": "ready.png"}])
    await fake_yandex_server.configure_directory(
        "pending-gif",
        images=[{"name": "pending.gif", "mime_type": "image/gif"}],
    )
    await fake_yandex_server.configure_directory("pending-photo", images=[{"name": "pending.png"}])
    await fake_yandex_server.configure_directory("empty", images=[])
    await fake_yandex_server.configure_directory(
        "broken",
        images=[{"name": "broken.gif", "mime_type": "image/gif"}],
    )
    await synchronize_functional_media_catalog()

    await fake_telegram_server.push_message(text="/ready-photo")
    await fake_telegram_server.wait_for_request("editMessageMedia")
    await fake_telegram_server.reset()
    await fake_yandex_server.reset()
    await fake_statsd_server.reset()

    await fake_telegram_server.push_inline_query(query=" / ", query_id="inline-blank")
    request = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-blank",
    )

    payload = request["payload"]
    assert_that(payload["cache_time"], equal_to(0))
    assert_that(payload["is_personal"], equal_to(True))
    results = payload["results"]
    assert_that(
        [result["title"] for result in results],
        equal_to(["🎲 /ready-photo", "🎲 /pending-gif", "🎲 /pending-photo"]),
    )
    results_by_title = {result["title"]: result for result in results}
    assert_that(
        results_by_title["🎲 /ready-photo"],
        has_entries(type="photo", photo_file_id="functional-photo-file-id"),
    )
    assert_that(
        results_by_title["🎲 /pending-gif"],
        has_entries(
            type="gif",
            gif_url=f"{fake_yandex_server.base_url}/download/pending.gif",
            thumbnail_url=f"{fake_yandex_server.base_url}/download/pending.gif",
        ),
    )
    assert_that(
        results_by_title["🎲 /pending-photo"],
        has_entries(
            type="photo",
            photo_url=f"{fake_yandex_server.base_url}/download/pending.png",
            thumbnail_url=f"{fake_yandex_server.base_url}/download/pending.png",
        ),
    )
    result_ids = [result["id"] for result in results]
    assert_that([len(result_id.encode()) for result_id in result_ids], equal_to([64, 64, 64]))
    assert_that(len(set(result_ids)), equal_to(3))

    yandex_requests = await fake_yandex_server.requests()
    url_lookups = [request for request in yandex_requests if request["method"] == "resources/download"]
    assert_that(
        [request["params"]["path"] for request in url_lookups],
        equal_to(
            [
                "app:/pending-gif/pending.gif",
                "app:/pending-photo/pending.png",
                "app:/broken/broken.gif",
            ]
        ),
    )
    assert_that([request for request in yandex_requests if request["method"] == "download"], equal_to([]))

    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.outcomes.partial_error.total",
        "functional.inline.scenarios.empty_query.total",
        "functional.inline.dependencies.postgres.calls",
        "functional.inline.dependencies.yandex.calls",
        "functional.inline.dependencies.telegram.calls",
        "functional.inline.media.catalog_items",
        "functional.inline.media.selected_items",
        "functional.inline.media.ready_items",
        "functional.inline.media.pending_items",
        "functional.inline.media.url_successes",
        "functional.inline.media.url_failures",
        "functional.inline.results.prepared",
        "functional.inline.results.sent",
    )
    assert_that(metrics["functional.inline.dependencies.postgres.calls"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(3))
    assert_that(metrics["functional.inline.dependencies.telegram.calls"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.media.catalog_items"]["value"], equal_to(4))
    assert_that(metrics["functional.inline.media.selected_items"]["value"], equal_to(4))
    assert_that(metrics["functional.inline.media.ready_items"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.media.pending_items"]["value"], equal_to(3))
    assert_that(metrics["functional.inline.media.url_successes"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.media.url_failures"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.results.prepared"]["value"], equal_to(3))
    assert_that(metrics["functional.inline.results.sent"]["value"], equal_to(3))


async def test_bot_returns_empty_inline_results_for_known_empty_category_and_keeps_polling(
    bot_process: subprocess.Process,
    fake_statsd_server: FakeStatsDServer,
    fake_telegram_server: FakeTelegramServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    fake_yandex_server: FakeYandexServer,
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/empty", time(0), "empty"),))
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()
    await fake_telegram_server.push_inline_query(query="empty", query_id="inline-empty")

    request = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-empty",
    )
    assert_that(request["payload"]["results"], empty())

    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.scenarios.known_empty.total",
        "functional.inline.stages.media.catalog",
        "functional.inline.dependencies.postgres.calls",
        "functional.inline.dependencies.yandex.calls",
    )
    assert_that(metrics["functional.inline.dependencies.postgres.calls"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(0))
    assert_that(await fake_statsd_server.metrics(name="functional.inline.stages.media.urls"), empty())

    await fake_telegram_server.push_message(text="still running after empty inline category")
    await fake_telegram_server.wait_for_request("sendMessage", predicate=_is_start_answer)


async def test_bot_returns_empty_inline_results_when_image_url_fails_and_keeps_polling(
    bot_process: subprocess.Process,
    fake_statsd_server: FakeStatsDServer,
    fake_telegram_server: FakeTelegramServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    fake_yandex_server: FakeYandexServer,
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/broken", time(0), "broken"),))
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()
    await fake_telegram_server.push_inline_query(query="broken", query_id="inline-broken")

    request = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-broken",
    )
    assert_that(request["payload"]["results"], empty())

    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.outcomes.partial_error.total",
        "functional.inline.scenarios.pending_only.total",
        "functional.inline.media.url_failures",
        "functional.inline.results.sent",
    )
    assert_that(metrics["functional.inline.media.url_failures"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.results.sent"]["value"], equal_to(0))

    await fake_telegram_server.push_message(text="still running after Yandex failure")
    await fake_telegram_server.wait_for_request("sendMessage", predicate=_is_start_answer)


async def test_inline_uses_persisted_file_id_after_ordinary_delivery(
    bot_process: subprocess.Process,
    fake_statsd_server: FakeStatsDServer,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await fake_yandex_server.configure_directory("day", images=[{"name": "image.png"}])
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    await fake_telegram_server.push_message(text="/day")
    await fake_telegram_server.wait_for_request("editMessageMedia")

    await fake_telegram_server.reset()
    await fake_yandex_server.reset()
    await fake_telegram_server.push_inline_query(query="day", query_id="inline-ready")
    answer = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-ready",
    )

    assert_that(answer["payload"]["results"], has_length(1))
    result = answer["payload"]["results"][0]
    assert_that(result, has_entries(type="photo", photo_file_id="functional-photo-file-id"))
    assert_that([key for key in result if key == "photo_url"], empty())
    assert_that(await fake_yandex_server.requests(), empty())

    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.scenarios.catalog_only.total",
        "functional.inline.media.ready_items",
        "functional.inline.media.pending_items",
        "functional.inline.dependencies.yandex.calls",
    )
    assert_that(metrics["functional.inline.media.ready_items"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.media.pending_items"]["value"], equal_to(0))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(0))


async def test_inline_metrics_cover_mixed_ready_and_pending_media(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    fake_statsd_server: FakeStatsDServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await fake_yandex_server.configure_directory(
        "day",
        images=[{"name": "first.png"}, {"name": "second.png"}],
    )
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    await fake_telegram_server.push_message(text="/day")
    await fake_telegram_server.wait_for_request("editMessageMedia")

    await fake_telegram_server.reset()
    await fake_yandex_server.reset()
    await fake_statsd_server.reset()
    await fake_telegram_server.push_inline_query(query="day", query_id="inline-mixed")
    answer = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-mixed",
    )

    assert_that(answer["payload"]["results"], has_length(2))
    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.scenarios.mixed.total",
        "functional.inline.media.ready_items",
        "functional.inline.media.pending_items",
        "functional.inline.dependencies.yandex.calls",
    )
    assert_that(metrics["functional.inline.media.ready_items"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.media.pending_items"]["value"], equal_to(1))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(1))


async def test_inline_metrics_cover_multiple_categories(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    fake_statsd_server: FakeStatsDServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    subscription_types = (
        FunctionalSubscriptionType(1, "/first", time(0), "first", ("shared",)),
        FunctionalSubscriptionType(2, "/second", time(0), "second", ("shared",)),
    )
    await seed_functional_subscription_types(subscription_types)
    await fake_yandex_server.configure_directory("first", images=[{"name": "first.png"}])
    await fake_yandex_server.configure_directory("second", images=[{"name": "second.png"}])
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()
    await fake_statsd_server.reset()

    await fake_telegram_server.push_inline_query(query="shared", query_id="inline-multiple")
    answer = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-multiple",
    )

    assert_that(answer["payload"]["results"], has_length(2))
    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.category_sets.multiple.total",
        "functional.inline.dependencies.postgres.calls",
        "functional.inline.dependencies.yandex.calls",
        "functional.inline.media.catalog_items",
    )
    assert_that(metrics["functional.inline.dependencies.postgres.calls"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(2))
    assert_that(metrics["functional.inline.media.catalog_items"]["value"], equal_to(2))


async def test_inline_metrics_cover_large_catalog_and_telegram_limit(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    fake_statsd_server: FakeStatsDServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/large", time(0), "large"),))
    await fake_yandex_server.configure_directory(
        "large",
        images=[{"name": f"image-{index}.png"} for index in range(60)],
    )
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()
    await fake_statsd_server.reset()

    await fake_telegram_server.push_inline_query(query="large", query_id="inline-large")
    answer = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-large",
    )

    assert_that(answer["payload"]["results"], has_length(50))
    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.catalog_sizes.large.total",
        "functional.inline.media.catalog_items",
        "functional.inline.dependencies.yandex.calls",
        "functional.inline.results.prepared",
        "functional.inline.results.sent",
    )
    assert_that(metrics["functional.inline.media.catalog_items"]["value"], equal_to(60))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(50))
    assert_that(metrics["functional.inline.results.prepared"]["value"], equal_to(50))
    assert_that(metrics["functional.inline.results.sent"]["value"], equal_to(50))


async def test_inline_uses_ready_media_to_fill_limit_without_pending_urls(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    fake_statsd_server: FakeStatsDServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/large", time(0), "large"),))
    await fake_yandex_server.configure_directory(
        "large",
        images=[{"name": f"image-{index}.png"} for index in range(51)],
    )
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids(
        {f"large/image-{index}.png": f"functional-ready-{index}" for index in range(50)}
    )
    await fake_telegram_server.reset()
    await fake_yandex_server.reset()
    await fake_statsd_server.reset()

    await fake_telegram_server.push_inline_query(query="large", query_id="inline-ready-limit")
    answer = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-ready-limit",
    )

    results = answer["payload"]["results"]
    assert_that(results, has_length(50))
    assert_that(results[0]["title"], equal_to("🎲 Выбрать случайную картинку"))
    assert_that(
        ["photo_file_id" in result and "photo_url" not in result for result in results],
        only_contains(True),
    )
    assert_that(await fake_yandex_server.requests(), empty())

    metrics = await _wait_for_metrics(
        fake_statsd_server,
        "functional.inline.media.catalog_items",
        "functional.inline.media.ready_items",
        "functional.inline.media.pending_items",
        "functional.inline.dependencies.yandex.calls",
    )
    assert_that(metrics["functional.inline.media.catalog_items"]["value"], equal_to(51))
    assert_that(metrics["functional.inline.media.ready_items"]["value"], equal_to(50))
    assert_that(metrics["functional.inline.media.pending_items"]["value"], equal_to(0))
    assert_that(metrics["functional.inline.dependencies.yandex.calls"]["value"], equal_to(0))


async def test_inline_excludes_media_deactivated_by_later_sync(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    seeded_subscription_types: tuple[FunctionalSubscriptionType, ...],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await fake_yandex_server.configure_directory(
        "day",
        images=[{"name": "image.png"}, {"name": "removed.png"}],
    )
    await synchronize_functional_media_catalog()
    await fake_yandex_server.configure_directory("day", images=[{"name": "image.png"}])
    await synchronize_functional_media_catalog()
    await fake_yandex_server.reset()

    await fake_telegram_server.push_inline_query(query="day", query_id="inline-active-only")
    answer = await fake_telegram_server.wait_for_request(
        "answerInlineQuery",
        predicate=lambda request: request["payload"].get("inline_query_id") == "inline-active-only",
    )

    assert_that(answer["payload"]["results"], has_length(1))
    assert_that(
        answer["payload"]["results"][0]["photo_url"],
        equal_to(f"{fake_yandex_server.base_url}/download/image.png"),
    )
    requests = await fake_yandex_server.requests()
    assert_that(sum(request["method"] == "resources/download" for request in requests), equal_to(1))
    assert_that([request for request in requests if "removed.png" in str(request)], empty())


def _is_start_answer(request: dict[str, Any]) -> bool:
    payload = request["payload"]
    return payload.get("chat_id") == 42 and "Что умеет бот" in payload.get("text", "")


async def _wait_for_metrics(
    fake_statsd_server: FakeStatsDServer,
    *names: str,
) -> dict[str, dict[str, Any]]:
    return {name: await fake_statsd_server.wait_for_metric(name) for name in names}


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

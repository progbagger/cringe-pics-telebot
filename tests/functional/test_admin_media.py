from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import time
from typing import Any

import aiohttp
import asyncpg
import pytest
from hamcrest import assert_that, contains_string, empty, equal_to, has_length

from tests.functional.conftest import (
    POSTGRES_ENV,
    DependencyPorts,
    FakeStatsDServer,
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)

CATEGORIES = tuple(FunctionalSubscriptionType(i, f"/cat{i:02}", time(8), f"cat{i:02}") for i in range(1, 10))


@dataclass(slots=True)
class MediaDatabase:
    connection: asyncpg.Connection

    async def seed(self, count: int, *, cached: bool = True) -> None:
        for index in range(count):
            kind, extension, mime = ("photo", "png", "image/png")
            if index % 3 == 1:
                kind, extension, mime = ("animation", "gif", "image/gif")
            elif index % 3 == 2:
                kind, extension, mime = ("video", "mp4", "video/mp4")

            # Repeated names deliberately have different paths.
            await self.connection.execute(
                """INSERT INTO category_media(
                    subscription_type_id, source_path, source_revision, name, mime_type, telegram_media_type,
                    telegram_file_id, telegram_file_unique_id, materialized_at, last_seen_at
                ) VALUES (9, $1, 'revision-1', $2, $3, $4, $5, $6,
                          CASE WHEN $5::text IS NULL THEN NULL ELSE now() END, now())""",
                f"cat09/{index}/same.{extension}",
                f"same.{extension}",
                mime,
                kind,
                f"preview-{index}" if cached else None,
                f"unique-{index}" if cached else None,
            )

            for position in range(index % 3):
                await self.connection.execute(
                    """INSERT INTO media_search_aliases(media_id, position, alias, normalized_alias)
                    SELECT id, $2, $3, $3 FROM category_media WHERE source_path = $1""",
                    f"cat09/{index}/same.{extension}",
                    position,
                    f"алиас {position}",
                )

    async def aliases(self, index: int) -> list[str]:
        rows = await self.connection.fetch(
            """SELECT alias FROM media_search_aliases WHERE media_id = (
                SELECT id FROM category_media WHERE source_path LIKE $1
            ) ORDER BY position""",
            f"cat09/{index}/%",
        )

        return [row["alias"] for row in rows]

    async def deactivate(self, *, index: int | None = None) -> None:
        await self.connection.execute(
            "UPDATE category_media SET is_active = false WHERE ($1::text IS NULL OR source_path LIKE $1)",
            f"cat09/{index}/%" if index is not None else None,
        )


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(CATEGORIES)


@pytest.fixture
async def media_database(docker_compose: DependencyPorts) -> AsyncIterator[MediaDatabase]:
    connection = await asyncpg.connect(
        user=POSTGRES_ENV["POSTGRES_USER"],
        password=POSTGRES_ENV["POSTGRES_PASSWORD"],
        database=POSTGRES_ENV["POSTGRES_DB"],
        host=POSTGRES_ENV["POSTGRES_HOST"],
        port=docker_compose.postgres,
    )

    try:
        yield MediaDatabase(connection)
    finally:
        await connection.close()


@dataclass(slots=True)
class MediaPanel:
    telegram: FakeTelegramServer
    statsd: FakeStatsDServer
    message_id: int
    payload: dict[str, Any]

    @property
    def texts(self) -> list[str]:
        return [b["text"] for row in self.payload["reply_markup"]["inline_keyboard"] for b in row]

    @property
    def media(self) -> list[tuple[str, str]]:
        return [
            (b["type"], b[b["type"]]["media"])
            for b in self.payload.get("rich_message", {}).get("blocks", [])
            if b["type"] in {"photo", "animation", "video"}
        ]

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.payload.get("rich_message", {}).get("blocks", []))

    def callback(self, text: str) -> str:
        return next(
            b["callback_data"]
            for row in self.payload["reply_markup"]["inline_keyboard"]
            for b in row
            if b["text"] == text
        )

    async def click(self, text: str) -> None:
        await self.follow(self.callback(text))

    async def follow(self, callback: str) -> None:
        edits = len(await self.telegram.requests(method="editMessageText"))
        answers = len(await self.telegram.requests(method="answerCallbackQuery"))

        transitions = len(await self.statsd.metrics(name="functional.admin_media.transition"))
        await self.telegram.push_callback_query(data=callback, message_id=self.message_id)
        await self.telegram.wait_for_requests("answerCallbackQuery", count=answers + 1)
        if callback.startswith("am:"):
            await self.statsd.wait_for_metric("functional.admin_media.transition", count=transitions + 1)

        requests = await self.telegram.requests(method="editMessageText")
        successful = [r for r in requests[edits:] if r.get("response", {}).get("ok")]
        if successful:
            self.payload = successful[-1]["payload"]

        assert all(r["payload"]["message_id"] == self.message_id for r in requests[edits:])

    async def input(self, text: str) -> None:
        previous = await self.telegram.requests(method="editMessageText")
        edits = len(previous)

        transitions = len(await self.statsd.metrics(name="functional.admin_media.transition"))
        await self.telegram.push_message(text=text)
        await self.statsd.wait_for_metric("functional.admin_media.transition", count=transitions + 1)
        await self.telegram.wait_for_requests("editMessageText", count=edits + 1)
        request = await self.telegram.wait_for_request(
            "editMessageText",
            predicate=lambda r: (
                "Алиасы медиа обновлены" in str(r["payload"])
                and r.get("response", {}).get("ok")
                and r["payload"]["message_id"] == self.message_id
                and r not in previous
            ),
        )

        self.payload = request["payload"]


async def open_panel(telegram: FakeTelegramServer, statsd: FakeStatsDServer) -> MediaPanel:
    panel = await prepare_category_panel(telegram, statsd)
    await panel.click("Медиа и алиасы")

    return panel


@pytest.mark.parametrize("count", [0, 1, 8, 11])
async def test_media_page_shows_ordered_original_media_and_alias_counts(
    count: int,
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(count)

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    # assert
    expected_count = min(8, count)
    assert_that(panel.texts[:expected_count], equal_to([f"{i + 1} · 🏷 {i % 3}" for i in range(expected_count)]))
    assert_that(
        panel.media, equal_to([(("photo", "animation", "video")[i % 3], f"preview-{i}") for i in range(expected_count)])
    )
    assert_that(panel.texts[expected_count:], equal_to(["Назад", ">"] if count > 8 else ["Назад"]))

    labels = [b["text"] for b in panel.payload["rich_message"]["blocks"][1:] if b["type"] == "paragraph"]
    assert_that(labels, equal_to([f"{i + 1}. same.{('png', 'gif', 'mp4')[i % 3]}" for i in range(expected_count)]))

    if count > 8:
        # act
        await panel.click(">")

        # assert
        assert_that(
            panel.media, equal_to([(("photo", "animation", "video")[i % 3], f"preview-{i}") for i in range(8, count)])
        )
        assert_that(panel.texts, equal_to([f"{i - 7} · 🏷 {i % 3}" for i in range(8, count)] + ["Назад", "<"]))

    if count:
        # act
        await panel.click(panel.texts[0])

        # assert
        selected_index = 8 if count > 8 else 0
        assert_that(
            panel.media, equal_to([(("photo", "animation", "video")[selected_index % 3], f"preview-{selected_index}")])
        )
        assert_that(panel.text, contains_string(f"Путь: cat09/{selected_index}/"))

    # assert
    assert_that(await media_database.connection.fetchval("SELECT count(*) FROM user_media_cycle_entries"), equal_to(0))
    assert_that(
        await media_database.connection.fetchval("SELECT count(*) FROM media_alias_enrichment_jobs"), equal_to(0)
    )
    assert_that(
        [
            r
            for r in await fake_telegram_server.requests()
            if r["method"] in {"sendPhoto", "sendAnimation", "sendVideo"}
        ],
        empty(),
    )


async def test_alias_form_updates_original_panel_and_returns_to_same_pages(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(9)

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)
    await panel.click(">")
    await panel.click("1 · 🏷 2")

    # assert
    assert_that(panel.media, equal_to([("video", "preview-8")]))

    # act
    await panel.click("Изменить алиасы медиа")

    # assert
    assert_that(panel.media, equal_to([("video", "preview-8")]))

    # act
    await fake_telegram_server.push_message(text=" \n ")
    await fake_telegram_server.wait_for_request(
        "sendMessage", predicate=lambda r: "Не найдено ни одного" in str(r["payload"])
    )

    # assert
    assert_that(await media_database.aliases(8), equal_to(["алиас 0", "алиас 1"]))

    # act
    await panel.input("  Сонный кот  \nсонный   кот\nСПИТ")

    # assert
    assert_that(await media_database.aliases(8), equal_to(["Сонный кот", "СПИТ"]))
    assert_that(panel.media, equal_to([("video", "preview-8")]))
    assert_that(panel.text, contains_string("• Сонный кот"))

    # act
    await panel.click("Очистить алиасы медиа")

    # assert
    assert_that(await media_database.aliases(8), empty())
    assert_that(panel.media, equal_to([("video", "preview-8")]))

    # act
    await panel.click("Изменить алиасы медиа")
    await panel.click("Отмена")

    # assert
    assert_that(panel.media, equal_to([("video", "preview-8")]))

    # act
    await panel.click("Назад")

    # assert
    assert_that(panel.texts, equal_to(["1 · 🏷 0", "Назад", "<"]))
    assert_that(panel.media, equal_to([("video", "preview-8")]))

    # act
    await panel.click("Назад")
    await panel.click("Назад")

    # assert
    assert_that(panel.texts, equal_to(["✅ /cat09 — активна", "Создать категорию", "Назад", "<"]))
    assert_that(await media_database.aliases(0), empty())

    requests = await fake_telegram_server.requests()
    assert_that(
        [r for r in requests if r["method"] in {"sendPhoto", "sendAnimation", "sendVideo", "sendRichMessage"}], empty()
    )
    assert_that(
        [r for r in requests if r["method"] == "sendMessage" and "Алиасы медиа обновлены" in str(r["payload"])], empty()
    )


@pytest.mark.parametrize("change", ["deactivate", "delete", "move"])
async def test_stale_number_never_edits_another_media(
    change: str,
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(9)

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    stale = panel.callback("1 · 🏷 0")

    match change:
        case "deactivate":
            await media_database.deactivate(index=0)
        case "delete":
            await media_database.connection.execute("DELETE FROM category_media WHERE source_path LIKE 'cat09/0/%'")
        case "move":
            await media_database.connection.execute(
                "UPDATE category_media SET subscription_type_id = 1 WHERE source_path LIKE 'cat09/0/%'"
            )

    await panel.follow(stale)

    # assert
    assert_that(panel.media[0], equal_to(("animation", "preview-1")))

    # act
    await panel.click("1 · 🏷 1")

    # assert
    assert_that(panel.text, contains_string("Путь: cat09/1/"))

    # act
    await panel.click("Изменить алиасы медиа")
    await media_database.deactivate(index=1)
    await fake_telegram_server.push_message(text="не сохранять")
    await fake_telegram_server.wait_for_request(
        "sendMessage", predicate=lambda r: r["payload"].get("text") == "Медиа больше недоступно."
    )

    # assert
    assert_that(await media_database.aliases(1), equal_to(["алиас 0"]))
    assert_that(await media_database.aliases(2), equal_to(["алиас 0", "алиас 1"]))


async def test_disappeared_page_clamps_to_last_page_then_empty_category(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(9)

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)
    await panel.click(">")
    await panel.click("1 · 🏷 2")
    await media_database.deactivate(index=8)
    await panel.click("Назад")

    # assert
    assert_that(panel.media, has_length(8))
    assert_that(panel.texts[-1], equal_to("Назад"))

    # act
    await panel.click("1 · 🏷 0")
    await media_database.deactivate()
    await panel.click("Назад")

    # assert
    assert_that(panel.media, empty())
    assert_that(panel.text, contains_string("Активных медиа пока нет"))
    assert_that(panel.texts, equal_to(["Назад"]))


async def test_pending_previews_use_urls_and_preserve_unavailable_position(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    fake_yandex_server: FakeYandexServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(3, cached=False)

    async with (
        aiohttp.ClientSession() as session,
        session.post(
            f"{fake_yandex_server.base_url}/test/download", json={"name": "same.gif", "url_status": 503}
        ) as response,
    ):
        response.raise_for_status()

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    # assert
    assert_that([kind for kind, _ in panel.media], equal_to(["photo", "video"]))
    assert_that(panel.text, contains_string("2. same.gif\nПредпросмотр недоступен"))
    assert_that(panel.media[0][1], contains_string("/download/same.png"))

    # act
    await panel.click("2 · 🏷 1")

    # assert
    assert_that(panel.text, contains_string("Путь: cat09/1/same.gif"))

    # act
    await panel.click("Изменить алиасы медиа")
    await panel.input("вручную")

    # assert
    assert_that(await media_database.aliases(1), equal_to(["вручную"]))
    assert_that(
        await media_database.connection.fetchval(
            "SELECT count(*) FROM category_media WHERE telegram_file_id IS NOT NULL"
        ),
        equal_to(2),
    )


async def test_rejected_attachment_keeps_numbered_controls_and_can_be_reopened(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(2)
    await fake_telegram_server.set_invalid_file_ids("preview-0")

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    # assert
    assert_that(panel.media, empty())
    assert_that(panel.texts, equal_to(["1 · 🏷 0", "2 · 🏷 1", "Назад"]))

    # act
    await panel.click("2 · 🏷 1")

    # assert
    assert_that(panel.media, equal_to([("animation", "preview-1")]))

    await fake_telegram_server.set_invalid_file_ids()

    # act
    await panel.click("Назад")

    # assert
    assert_that(panel.media, has_length(2))


async def test_telegram_error_never_pairs_new_controls_with_old_previews(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(9)

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    original_payload = panel.payload
    await errors(fake_telegram_server, "Bad Request: message cannot be edited")
    await panel.click(">")

    # assert
    assert panel.payload == original_payload

    # act
    await panel.click(">")

    # assert
    assert_that(panel.media, equal_to([("video", "preview-8")]))

    # act
    await panel.click("1 · 🏷 2")
    await panel.click("Изменить алиасы медиа")
    await errors(fake_telegram_server, "Bad Request: message to edit not found")
    await fake_telegram_server.push_message(text="сохранено")
    await fake_telegram_server.wait_for_request(
        "sendMessage", predicate=lambda r: "Алиасы медиа сохранены." in str(r["payload"])
    )

    # assert
    assert_that(await media_database.aliases(8), equal_to(["сохранено"]))

    # act
    await panel.click("Отмена")

    # assert
    assert_that(panel.text, contains_string("• сохранено"))

    await errors(fake_telegram_server, "Bad Request: message is not modified")

    # act
    await panel.click("Отмена" if "Отмена" in panel.texts else "Изменить алиасы медиа")
    answers = await fake_telegram_server.requests(method="answerCallbackQuery")

    # assert
    assert answers[-1]["payload"].get("text") != "Не удалось выполнить действие."


async def test_non_administrator_cannot_use_captured_media_controls(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(1)

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    captured = [panel.callback("1 · 🏷 0")]
    await panel.click("1 · 🏷 0")
    captured.append(panel.callback("Изменить алиасы медиа"))

    edits = len(await fake_telegram_server.requests(method="editMessageText"))
    for callback in captured:
        await fake_telegram_server.push_callback_query(data=callback, user_id=99, message_id=panel.message_id)

    # A following handled admin update is the barrier for processing the queued updates.
    await panel.click("Назад")

    # assert
    assert len(await fake_telegram_server.requests(method="editMessageText")) == edits + 1
    assert_that(await media_database.aliases(0), empty())


async def errors(telegram: FakeTelegramServer, description: str) -> None:
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            f"{telegram.base_url}/test/media-errors",
            json={"responses": [{"error_code": 400, "description": description}]},
        ) as response,
    ):
        response.raise_for_status()


@pytest.mark.parametrize("cached", [False, True], ids=["cold", "warm"])
async def test_preview_warms_all_types_and_alias_transitions_reuse_file_ids(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    fake_yandex_server: FakeYandexServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    cached: bool,
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(8, cached=cached)
    before = len(await url_requests(fake_yandex_server))
    original_aliases = [await media_database.aliases(index) for index in range(8)]

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    rows = await media_database.connection.fetch("SELECT * FROM category_media ORDER BY source_path")

    # assert
    assert_that([row["telegram_file_id"] for row in rows], has_length(8))
    assert all(row["telegram_file_id"] and row["telegram_file_unique_id"] and row["materialized_at"] for row in rows)
    assert len(await url_requests(fake_yandex_server)) - before == (0 if cached else 8)
    assert_that([await media_database.aliases(index) for index in range(8)], equal_to(original_aliases))
    assert await media_database.connection.fetchval("SELECT count(*) FROM user_media_cycle_states") == 0
    assert await media_database.connection.fetchval("SELECT count(*) FROM media_alias_enrichment_jobs") == 0

    # Compare actual response blocks with their concrete media rows, including repeated names.
    responses = await fake_telegram_server.requests(method="editMessageText")
    shown = [b for b in responses[-1]["response"]["result"]["rich_message"]["blocks"] if b["type"] != "paragraph"]
    for row, block in zip(rows, shown, strict=True):
        file = block["photo"][-1] if block["type"] == "photo" else block[block["type"]]
        assert row["telegram_file_id"] == file["file_id"]
        if not cached:
            assert row["telegram_file_unique_id"] == file["file_unique_id"]

    # act
    await panel.click("1 · 🏷 0")
    await panel.click("Изменить алиасы медиа")
    await panel.input("новый алиас")
    await panel.click("Очистить алиасы медиа")
    await panel.click("Изменить алиасы медиа")
    await panel.click("Отмена")
    await panel.click("Назад")
    await panel.click("1 · 🏷 0")

    # assert
    assert len(await url_requests(fake_yandex_server)) - before == (0 if cached else 8)
    assert_that(panel.media, equal_to([("photo", rows[0]["telegram_file_id"])]))
    for name in ("preview_prepare", "telegram_edit", "transition", "cache_hit"):
        await fake_statsd_server.wait_for_metric(f"functional.admin_media.{name}")
    assert sum(m["value"] for m in await fake_statsd_server.metrics(name="functional.admin_media.cache_miss")) == (
        0 if cached else 8
    )
    if not cached:
        await fake_statsd_server.wait_for_metric("functional.admin_media.url_lookup")
    metrics = await fake_statsd_server.metrics()
    timings = {
        name: [m["value"] for m in metrics if m["name"] == f"functional.admin_media.{name}"]
        for name in ("preview_prepare", "url_lookup", "telegram_edit", "transition")
    }
    print(
        f"\nissue-122 cached={cached} lookup={len(await url_requests(fake_yandex_server)) - before} "
        f"timings_ms={timings}"
    )


async def url_requests(yandex: FakeYandexServer) -> list[dict[str, Any]]:
    return [r for r in await yandex.requests() if r["method"] == "resources/download"]


async def configure_url(yandex: FakeYandexServer, path: str, *, block: bool) -> None:
    async with (
        aiohttp.ClientSession() as session,
        session.post(f"{yandex.base_url}/test/url", json={"path": f"app:/{path}", "block": block}) as response,
    ):
        response.raise_for_status()


async def wait_urls(yandex: FakeYandexServer, count: int) -> dict[str, int]:
    async with (
        aiohttp.ClientSession() as session,
        session.get(f"{yandex.base_url}/test/wait-urls", params={"count": count}) as response,
    ):
        response.raise_for_status()
        return (await response.json())["result"]


async def prepare_category_panel(telegram: FakeTelegramServer, statsd: FakeStatsDServer) -> MediaPanel:
    await telegram.push_message(text="/admin")
    request = await telegram.wait_for_request(
        "sendMessage",
        predicate=lambda r: "Управление категориями" in str(r["payload"]) and r.get("response", {}).get("ok"),
    )
    panel = MediaPanel(telegram, statsd, request["response"]["result"]["message_id"], request["payload"])
    await panel.click("Управление категориями")
    await panel.click(">")
    await panel.click("✅ /cat09 — активна")
    return panel


async def test_cold_page_url_lookups_are_bounded_ordered_and_acknowledged_early(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    fake_yandex_server: FakeYandexServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(8, cached=False)
    rows = await media_database.connection.fetch("SELECT source_path FROM category_media ORDER BY source_path")
    for row in rows:
        await configure_url(fake_yandex_server, row["source_path"], block=True)
    panel = await prepare_category_panel(fake_telegram_server, fake_statsd_server)
    answers = len(await fake_telegram_server.requests(method="answerCallbackQuery"))
    before = len(await url_requests(fake_yandex_server))
    transition = len(await fake_statsd_server.metrics(name="functional.admin_media.transition"))

    # act
    await fake_telegram_server.push_callback_query(data=panel.callback("Медиа и алиасы"), message_id=panel.message_id)
    await fake_telegram_server.wait_for_requests("answerCallbackQuery", count=answers + 1)
    stats = await wait_urls(fake_yandex_server, before + 4)

    # assert
    assert stats["max_active"] == 4
    assert len(await url_requests(fake_yandex_server)) == before + 4
    assert (
        await media_database.connection.fetchval(
            "SELECT count(*) FROM category_media WHERE telegram_file_id IS NOT NULL"
        )
        == 0
    )

    # act
    # Release in reverse order; a later slot must start before the first lookup finishes.
    for index in (3, 2, 1):
        await configure_url(fake_yandex_server, rows[index]["source_path"], block=False)
    await wait_urls(fake_yandex_server, before + 7)
    for row in rows:
        await configure_url(fake_yandex_server, row["source_path"], block=False)
    await fake_statsd_server.wait_for_metric("functional.admin_media.transition", count=transition + 1)

    # assert
    request = (await fake_telegram_server.requests(method="editMessageText"))[-1]
    panel.payload = request["payload"]
    assert_that(
        [kind for kind, _ in panel.media],
        equal_to(["photo", "animation", "video", "photo", "animation", "video", "photo", "animation"]),
    )
    assert (await wait_urls(fake_yandex_server, before + 8))["max_active"] == 4
    assert len(await fake_telegram_server.requests(method="answerCallbackQuery")) == answers + 1


async def test_url_timeout_keeps_other_previews_and_their_original_positions(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    fake_yandex_server: FakeYandexServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(3, cached=False)
    await configure_url(fake_yandex_server, "cat09/1/same.gif", block=True)

    # act
    panel = await open_panel(fake_telegram_server, fake_statsd_server)

    # assert
    assert_that([kind for kind, _ in panel.media], equal_to(["photo", "video"]))
    assert_that(panel.text, contains_string("2. same.gif\nПредпросмотр недоступен"))
    ids = await media_database.connection.fetch("SELECT telegram_file_id FROM category_media ORDER BY source_path")
    assert_that(
        [row["telegram_file_id"] for row in ids],
        equal_to(["functional-photo-file-id", None, "functional-video-file-id"]),
    )
    await configure_url(fake_yandex_server, "cat09/1/same.gif", block=False)


@pytest.mark.parametrize("change", ["revision", "inactive", "materialized"])
async def test_preview_response_cannot_overwrite_changed_or_concurrently_materialized_media(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    change: str,
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(1, cached=False)

    panel = await prepare_category_panel(fake_telegram_server, fake_statsd_server)
    edits = len(await fake_telegram_server.requests(method="editMessageText"))
    answers = len(await fake_telegram_server.requests(method="answerCallbackQuery"))
    transitions = len(await fake_statsd_server.metrics(name="functional.admin_media.transition"))
    await fake_telegram_server.block_method("editMessageText")

    # act
    await fake_telegram_server.push_callback_query(data=panel.callback("Медиа и алиасы"), message_id=panel.message_id)
    await fake_telegram_server.wait_for_requests("editMessageText", count=edits + 1)

    # assert
    assert len(await fake_telegram_server.requests(method="answerCallbackQuery")) == answers + 1

    # arrange
    if change == "revision":
        await media_database.connection.execute("UPDATE category_media SET source_revision = 'revision-2'")
    elif change == "inactive":
        await media_database.deactivate()
    else:
        await media_database.connection.execute(
            "UPDATE category_media SET telegram_file_id = 'concurrent', "
            "telegram_file_unique_id = 'concurrent-unique', materialized_at = now()"
        )

    # act
    await fake_telegram_server.release_method("editMessageText")
    await fake_statsd_server.wait_for_metric("functional.admin_media.transition", count=transitions + 1)

    # assert
    assert await media_database.connection.fetchval("SELECT telegram_file_id FROM category_media") == (
        "concurrent" if change == "materialized" else None
    )
    assert len(await fake_telegram_server.requests(method="answerCallbackQuery")) == answers + 1


@pytest.mark.parametrize("description", ["Bad Request: wrong file identifier", "Bad Request: message cannot be edited"])
async def test_failed_edit_does_not_warm_cache_and_error_after_ack_is_visible(
    media_database: MediaDatabase,
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    description: str,
) -> None:
    # arrange
    await set_functional_administrator(user_id=42)
    await media_database.seed(3, cached=False)
    panel = await prepare_category_panel(fake_telegram_server, fake_statsd_server)
    await errors(fake_telegram_server, description)
    answers = len(await fake_telegram_server.requests(method="answerCallbackQuery"))

    # act
    await panel.click("Медиа и алиасы")

    # assert
    assert (
        await media_database.connection.fetchval(
            "SELECT count(*) FROM category_media WHERE telegram_file_id IS NOT NULL"
        )
        == 0
    )
    requests = await fake_telegram_server.requests(method="sendMessage")
    notice = "Предпросмотры недоступны" if "file identifier" in description else "Не удалось выполнить действие"
    assert any(notice in r["payload"].get("text", "") for r in requests)
    assert len(await fake_telegram_server.requests(method="answerCallbackQuery")) == answers + 1

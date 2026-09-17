import asyncio
import base64
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from typing import Any

import av
import pytest
from hamcrest import assert_that, contains_exactly, contains_string, empty, has_entries, has_length
from PIL import Image
from redis import asyncio as redis

from cringe_pics_telebot.bot.admin_panel_callback_data import AdminPanelAction, AdminPanelCallbackData
from cringe_pics_telebot.services.media_sync import MEDIA_SYNC_LEASE_KEY
from tests.functional.conftest import (
    REDIS_ENV,
    DependencyPorts,
    FakeStatsDServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)
from tests.functional.enrichment_support import EnrichmentBot, EnrichmentDatabase, FakeOllamaServer


def _media_bytes(mime_type: str) -> bytes:
    with BytesIO() as output:
        colors = ((220, 20, 20), (20, 220, 20), (20, 20, 220))
        if mime_type == "video/mp4":
            with av.open(output, mode="w", format="mp4") as container:
                stream = container.add_stream("mpeg4", rate=1)
                stream.width = stream.height = 16
                stream.pix_fmt = "yuv420p"

                for color in colors + colors[:2]:
                    with Image.new("RGB", (16, 16), color) as image:
                        for packet in stream.encode(av.VideoFrame.from_image(image)):
                            container.mux(packet)

                for packet in stream.encode():
                    container.mux(packet)
        else:
            frames = [Image.new("RGB", (16, 16), color) for color in colors]
            try:
                if mime_type == "image/gif":
                    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=100)
                else:
                    frames[0].save(output, format="PNG")
            finally:
                for frame in frames:
                    frame.close()

        return output.getvalue()


async def _synchronize(bot: EnrichmentBot, *, message_id: int = 100) -> dict[str, Any]:
    await bot.telegram.push_callback_query(
        data=AdminPanelCallbackData(action=AdminPanelAction.synchronize_media).pack(),
        message_id=message_id,
    )
    return await bot.telegram.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("message_id") == message_id
            and "<b>Синхронизация медиа завершена" in request["payload"].get("text", "")
        ),
    )


@pytest.fixture
async def enrichment_category(
    *,
    enrichment_bot: EnrichmentBot,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    fake_yandex_server: FakeYandexServer,
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/test", None, "test"),))
    await set_functional_administrator(user_id=42)
    await fake_yandex_server.configure_directory("test", images=[{"name": "image.png"}])


@pytest.mark.parametrize(
    ("mime_type", "name", "dominant_channel"),
    [("image/png", "image.png", 0), ("image/gif", "image.gif", 1), ("video/mp4", "video.mp4", 2)],
)
async def test_worker_prepares_representative_frame_and_inline_sees_normalized_aliases(
    *,
    mime_type: str,
    name: str,
    dominant_channel: int,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_yandex_server: FakeYandexServer,
    fake_ollama_server: FakeOllamaServer,
    fake_statsd_server: FakeStatsDServer,
    read_functional_media_search_aliases: Callable[[str], Awaitable[tuple[str, ...] | None]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
) -> None:
    source = _media_bytes(mime_type)
    await fake_yandex_server.configure_directory(
        "test",
        images=[{"name": name, "mime_type": mime_type, "size": len(source)}],
    )
    await fake_yandex_server.configure_download(name=name, content=source, content_type=mime_type)

    result = await _synchronize(enrichment_bot)
    assert_that(result["payload"]["text"], contains_string("Поставлено заданий на алиасы: <b>1</b>"))

    jobs = await enrichment_database.wait_for_jobs(lambda jobs: len(jobs) == 1 and jobs[0]["status"] == "succeeded")
    assert_that(jobs, contains_exactly(has_entries(attempt_count=1, retry_count=1, result_class="succeeded")))
    assert_that(await read_functional_media_search_aliases(f"test/{name}"), contains_exactly("Сонный кот", "Кофе"))

    requests = await fake_ollama_server.requests(wait_for=1)
    assert_that(requests, has_length(1))
    assert requests[0]["authorization"] == "Bearer functional-ollama-key"
    payload = requests[0]["payload"]
    assert payload["model"] == "functional-vision-model"
    assert payload["stream"] is False

    encoded = payload["messages"][0]["images"][0]
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        assert image.format == "JPEG"
        channels = image.getpixel((8, 8))
        assert isinstance(channels, tuple)
        assert channels[dominant_channel] > max(
            value for index, value in enumerate(channels) if index != dominant_channel
        )

    downloads = [request for request in await fake_yandex_server.requests() if request["method"] == "download"]
    assert_that(downloads, contains_exactly(has_entries(path=name, authorization=None)))

    await set_functional_category_media_file_ids({f"test/{name}": "enriched-file-id"})
    await enrichment_bot.telegram.push_inline_query(query="сонный кот", query_id="enriched-search")
    answer = await enrichment_bot.telegram.wait_for_request("answerInlineQuery")
    assert_that(answer["payload"]["results"], has_length(1))
    assert "enriched-file-id" in answer["payload"]["results"][0].values()

    for metric in ("succeeded", "media_prepare", "ollama_request", "queue.available"):
        await fake_statsd_server.wait_for_metric(f"functional.media_alias_enrichment.{metric}")

    await enrichment_bot.stop()
    logs = "".join(enrichment_bot.logs)
    assert "enrichment pass failed" not in logs
    for private in (payload["messages"][0]["content"], "functional-ollama-key", encoded, "Сонный кот", "Кофе"):
        assert private not in logs


@pytest.mark.parametrize("mutation", ["manual_aliases", "inactive", "revision"])
async def test_worker_does_not_save_result_when_media_changes_during_ollama(
    *,
    mutation: str,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_ollama_server: FakeOllamaServer,
    read_functional_media_search_aliases: Callable[[str], Awaitable[tuple[str, ...] | None]],
    docker_compose: DependencyPorts,
) -> None:
    await fake_ollama_server.configure(block=True)
    await _synchronize(enrichment_bot)
    await fake_ollama_server.requests(wait_for=1)

    async with redis.Redis(
        host=REDIS_ENV["REDIS_HOST"],
        port=docker_compose.redis,
        username=REDIS_ENV["REDIS_USERNAME"],
        password=REDIS_ENV["REDIS_PASSWORD"],
    ) as client:
        assert await client.exists(MEDIA_SYNC_LEASE_KEY) == 0

    job = (await enrichment_database.jobs())[0]
    # If network I/O held a row-locked transaction, this bounded mutation would hang.
    async with asyncio.timeout(2), enrichment_database.connection.transaction():
        await enrichment_database.connection.execute(
            "SELECT id FROM category_media WHERE id=$1 FOR UPDATE", job["media_id"]
        )

        if mutation == "manual_aliases":
            await enrichment_database.connection.execute(
                "INSERT INTO media_search_aliases VALUES ($1, 0, 'Ручной алиас', 'ручной алиас')",
                job["media_id"],
            )
        elif mutation == "inactive":
            await enrichment_database.connection.execute(
                "UPDATE category_media SET is_active=false WHERE id=$1", job["media_id"]
            )
        else:
            await enrichment_database.connection.execute(
                "UPDATE category_media SET source_revision='sha256:new' WHERE id=$1",
                job["media_id"],
            )

    await fake_ollama_server.release()

    expected = {"manual_aliases": "manual_aliases_won", "inactive": "inactive", "revision": "stale_revision"}[mutation]
    jobs = await enrichment_database.wait_for_jobs(
        lambda jobs: any(current["id"] == job["id"] and current["result_class"] == expected for current in jobs),
    )
    assert jobs[0]["status"] == "obsolete"

    if mutation == "manual_aliases":
        assert_that(await read_functional_media_search_aliases("test/image.png"), contains_exactly("Ручной алиас"))
    elif mutation == "inactive":
        assert_that(await read_functional_media_search_aliases("test/image.png"), empty())
    else:
        jobs = await enrichment_database.wait_for_jobs(lambda jobs: len(jobs) == 2 and jobs[1]["status"] == "succeeded")
        assert_that(
            jobs,
            contains_exactly(
                has_entries(source_revision=job["source_revision"], status="obsolete"),
                has_entries(source_revision="sha256:new", status="succeeded"),
            ),
        )


@pytest.mark.parametrize(
    "response", [{"status": 429}, {"status": 503}, {"body": "not-json"}, {"content": '{"aliases":["  "]}'}]
)
async def test_retry_backoff_failure_budget_and_sync_reopen_are_persistent(
    *,
    response: dict[str, Any],
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_ollama_server: FakeOllamaServer,
) -> None:
    await fake_ollama_server.configure(responses=[response])
    await _synchronize(enrichment_bot)
    jobs = await enrichment_database.wait_for_jobs(lambda jobs: len(jobs) == 1 and jobs[0]["status"] == "retry")
    assert jobs[0]["available_at"] - jobs[0]["updated_at"] == timedelta(seconds=30)
    assert jobs[0]["attempt_count"] == 1

    await enrichment_database.connection.execute(
        "UPDATE media_alias_enrichment_jobs SET available_at=now()-interval '1 second'"
    )

    jobs = await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == "failed")
    assert jobs[0]["attempt_count"] == 2
    assert jobs[0]["retry_count"] == 2

    await fake_ollama_server.configure()
    result = await _synchronize(enrichment_bot, message_id=101)
    assert_that(result["payload"]["text"], contains_string("Поставлено заданий на алиасы: <b>1</b>"))
    jobs = await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == "succeeded")
    assert jobs[0]["attempt_count"] == 3
    assert jobs[0]["retry_count"] == 1


@pytest.mark.parametrize(
    ("failure", "response", "expected_status", "expected_result"),
    [
        ("bad_request", {"status": 400}, "failed", "http_400"),
        ("unauthorized", {"status": 401}, "failed", "http_401"),
        ("rate_limit", {"status": 429}, "retry", "http_429"),
        ("server_error", {"status": 503}, "retry", "http_503"),
        ("invalid_output", {"body": "not-json"}, "retry", "invalid_output"),
        ("timeout", {}, "retry", "network_error"),
        ("disconnect", {"disconnect": True}, "retry", "network_error"),
        ("unavailable", {}, "retry", "network_error"),
    ],
)
async def test_ollama_failure_does_not_stop_ordinary_or_scheduled_delivery(
    *,
    failure: str,
    response: dict[str, Any],
    expected_status: str,
    expected_result: str,
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
    enrichment_database: EnrichmentDatabase,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    fake_yandex_server: FakeYandexServer,
    fake_ollama_server: FakeOllamaServer,
    unused_tcp_port: int,
) -> None:
    await fake_ollama_server.configure(responses=[response], block=failure == "timeout")
    overrides = {"OLLAMA_REQUEST_TIMEOUT_SECONDS": "0.1"} if failure == "timeout" else {}
    if failure == "unavailable":
        overrides["OLLAMA_BASE_URL"] = f"http://127.0.0.1:{unused_tcp_port}"

    async with start_enrichment_bot(overrides=overrides, subscription_now=datetime(2026, 9, 17, 10, tzinfo=UTC)) as bot:
        await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/test", None, "test"),))
        await set_functional_administrator(user_id=42)
        await create_user_subscription(user_id=700, subscription_type_id=1, timezone_offset_minutes=0)
        await fake_yandex_server.configure_directory("test", images=[{"name": "image.png"}])

        await _synchronize(bot)
        jobs = await enrichment_database.wait_for_jobs(
            lambda jobs: len(jobs) == 1 and jobs[0]["status"] == expected_status
        )
        assert_that(jobs, contains_exactly(has_entries(result_class=expected_result, attempt_count=1)))

        update = await bot.telegram.push_message(text="/test", user_id=42)
        ordinary = await bot.telegram.wait_for_request(
            "editMessageMedia", predicate=lambda request: int(request["payload"]["chat_id"]) == 42
        )
        assert_that(
            ordinary["payload"]["media"],
            has_entries(type="photo", media=f"{fake_yandex_server.base_url}/download/image.png"),
        )
        await bot.wait_for_log(f"Update id={update['result']['update_id']} is handled")

        # Enable a due schedule only after the failure and ordinary delivery were observed.
        # The real scheduler loop shares this bot process; its clock is fixed, not slept through.
        await enrichment_database.connection.execute("UPDATE subscription_types SET time='10:00' WHERE id=1")
        scheduled = await bot.telegram.wait_for_request(
            "sendPhoto", predicate=lambda request: int(request["payload"]["chat_id"]) == 700
        )
        assert scheduled["payload"]["photo"] == "functional-photo-file-id"

        assert bot.process.returncode is None
        assert "enrichment pass failed" not in "".join(bot.logs)


async def test_shutdown_releases_reservation_and_another_process_recovers_expired_lease(
    *,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_ollama_server: FakeOllamaServer,
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
) -> None:
    await fake_ollama_server.configure(block=True)
    await _synchronize(enrichment_bot)
    await fake_ollama_server.requests(wait_for=1)
    first = (await enrichment_database.jobs())[0]

    await enrichment_bot.stop()
    jobs = await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == "retry")
    assert jobs[0]["result_class"] == "cancelled"
    assert jobs[0]["lease_token"] is None

    await enrichment_database.connection.execute("""
        UPDATE media_alias_enrichment_jobs SET status='processing', lease_token='abandoned',
        leased_until=now()-interval '1 second', finished_at=NULL
    """)
    await fake_ollama_server.release()

    async with start_enrichment_bot():
        jobs = await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == "succeeded")

    assert jobs[0]["attempt_count"] == first["attempt_count"] + 1
    assert_that(await fake_ollama_server.requests(), has_length(2))


async def test_two_processes_do_not_claim_live_lease_and_heartbeat_loss_cancels_old_job(
    *,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_ollama_server: FakeOllamaServer,
    fake_statsd_server: FakeStatsDServer,
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
) -> None:
    await fake_ollama_server.configure(block=True)
    async with start_enrichment_bot() as second:
        await _synchronize(enrichment_bot)
        await fake_ollama_server.requests(wait_for=1)
        first = (await enrichment_database.jobs())[0]
        await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["leased_until"] > first["leased_until"])
        assert_that(await fake_ollama_server.requests(), has_length(1))

        await enrichment_database.connection.execute(
            "UPDATE media_alias_enrichment_jobs SET lease_token='new-owner' WHERE id=$1",
            first["id"],
        )

        await fake_statsd_server.wait_for_metric("functional.media_alias_enrichment.lease_lost")
        await fake_ollama_server.release()
        await enrichment_bot.stop()
        await second.stop()

    jobs = await enrichment_database.jobs()
    assert_that(jobs, contains_exactly(has_entries(status="processing", lease_token="new-owner")))


@pytest.mark.parametrize(
    ("kind", "overrides", "expected"),
    [
        ("decode", {}, "decode_failed"),
        ("source_limit", {"MEDIA_ALIAS_ENRICHMENT_MAX_SOURCE_BYTES": "20"}, "source_too_large"),
        ("frame_limit", {"MEDIA_ALIAS_ENRICHMENT_MAX_FRAME_PIXELS": "255"}, "frame_too_large"),
        ("image_limit", {"MEDIA_ALIAS_ENRICHMENT_MAX_IMAGE_BYTES": "1"}, "image_too_large"),
        ("ollama_400", {}, "http_400"),
        ("yandex_404", {}, "http_404"),
    ],
)
async def test_permanent_media_or_http_failure_is_not_retried(
    *,
    kind: str,
    overrides: dict[str, str],
    expected: str,
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
    enrichment_database: EnrichmentDatabase,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    fake_yandex_server: FakeYandexServer,
    fake_ollama_server: FakeOllamaServer,
) -> None:
    async with start_enrichment_bot(overrides=overrides) as bot:
        await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/test", None, "test"),))
        await set_functional_administrator(user_id=42)
        await fake_yandex_server.configure_directory("test", images=[{"name": "image.png"}])
        source = b"invalid image" if kind == "decode" else _media_bytes("image/png")
        await fake_yandex_server.configure_download(
            name="image.png", content=source, status=404 if kind == "yandex_404" else 200
        )
        if kind == "ollama_400":
            await fake_ollama_server.configure(responses=[{"status": 400}])

        await _synchronize(bot)
        jobs = await enrichment_database.wait_for_jobs(lambda jobs: len(jobs) == 1 and jobs[0]["status"] == "failed")

        assert_that(jobs, contains_exactly(has_entries(attempt_count=1, retry_count=1, result_class=expected)))
        assert jobs[0]["leased_until"] is None
        assert jobs[0]["lease_token"] is None
        requests = await fake_ollama_server.requests()
        assert len(requests) == (1 if kind == "ollama_400" else 0)


async def test_failed_job_does_not_cancel_other_media_in_same_batch(
    *,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_yandex_server: FakeYandexServer,
    fake_ollama_server: FakeOllamaServer,
) -> None:
    await fake_yandex_server.configure_directory("test", images=[{"name": "corrupt.png"}, {"name": "good.png"}])
    await fake_yandex_server.configure_download(name="corrupt.png", content=b"invalid image")

    await _synchronize(enrichment_bot)
    jobs = await enrichment_database.wait_for_jobs(
        lambda jobs: len(jobs) == 2 and {job["status"] for job in jobs} == {"failed", "succeeded"},
    )

    assert_that(
        jobs,
        contains_exactly(
            has_entries(source_path="test/corrupt.png", status="failed", result_class="decode_failed"),
            has_entries(source_path="test/good.png", status="succeeded"),
        ),
    )
    assert_that(await fake_ollama_server.requests(), has_length(1))


async def test_existing_pending_ready_and_inactive_category_media_are_enriched_but_manual_aliases_are_not(
    *,
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
    enrichment_database: EnrichmentDatabase,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
    set_functional_media_search_aliases: Callable[[dict[str, tuple[str, ...]]], Awaitable[None]],
    read_functional_media_search_aliases: Callable[[str], Awaitable[tuple[str, ...] | None]],
    fake_ollama_server: FakeOllamaServer,
) -> None:
    async with start_enrichment_bot(overrides={"MEDIA_ALIAS_ENRICHMENT_ENABLED": "false"}) as off:
        await seed_functional_subscription_types(
            (
                FunctionalSubscriptionType(1, "/ready", None, "ready"),
                FunctionalSubscriptionType(2, "/pending", None, "pending", is_active=False),
                FunctionalSubscriptionType(3, "/manual", None, "manual"),
            )
        )
        await set_functional_administrator(user_id=42)

        await _synchronize(off)
        await set_functional_category_media_file_ids({"ready/image.png": "existing-file-id"})
        await set_functional_media_search_aliases({"manual/image.png": ("Авторский алиас",)})
        assert_that(await enrichment_database.jobs(), empty())

    async with start_enrichment_bot() as on:
        jobs = await enrichment_database.wait_for_jobs(
            lambda jobs: len(jobs) == 2 and all(job["status"] == "succeeded" for job in jobs),
        )

        assert_that(
            jobs,
            contains_exactly(
                has_entries(source_path="pending/image.png", status="succeeded"),
                has_entries(source_path="ready/image.png", status="succeeded"),
            ),
        )
        assert_that(await read_functional_media_search_aliases("manual/image.png"), contains_exactly("Авторский алиас"))

        result = await _synchronize(on)
        assert_that(result["payload"]["text"], contains_string("Поставлено заданий на алиасы: <b>0</b>"))
        assert_that(await fake_ollama_server.requests(), has_length(2))


async def test_clearing_aliases_reopens_same_revision_without_resetting_total_attempts(
    *,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    set_functional_media_search_aliases: Callable[[dict[str, tuple[str, ...]]], Awaitable[None]],
    fake_ollama_server: FakeOllamaServer,
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
) -> None:
    await _synchronize(enrichment_bot)
    jobs = await enrichment_database.wait_for_jobs(lambda jobs: len(jobs) == 1 and jobs[0]["status"] == "succeeded")
    first_id = jobs[0]["id"]

    await enrichment_bot.stop()
    async with start_enrichment_bot(
        overrides={
            "OLLAMA_MODEL": "changed-model",
            "MEDIA_ALIAS_LLM_PROMPT": "Changed private prompt",
        }
    ) as changed:
        # A new model/prompt does not regenerate nonempty aliases on startup/sync.
        assert_that(await fake_ollama_server.requests(), has_length(1))
        await set_functional_media_search_aliases({"test/image.png": ()})

        await _synchronize(changed, message_id=101)
        jobs = await enrichment_database.wait_for_jobs(
            lambda jobs: jobs[0]["status"] == "succeeded" and jobs[0]["attempt_count"] == 2
        )

    assert_that(jobs, contains_exactly(has_entries(id=first_id, attempt_count=2, retry_count=1)))
    assert jobs[0]["model"] == "changed-model"
    assert jobs[0]["prompt_sha256"] == sha256(b"Changed private prompt").hexdigest()
    assert_that(await fake_ollama_server.requests(), has_length(2))


async def test_disabled_feature_ignores_ollama_config_and_does_not_create_worker(
    *,
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
    enrichment_database: EnrichmentDatabase,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    set_functional_administrator: Callable[..., Awaitable[None]],
    fake_ollama_server: FakeOllamaServer,
    fake_statsd_server: FakeStatsDServer,
) -> None:
    async with start_enrichment_bot(
        overrides={
            "MEDIA_ALIAS_ENRICHMENT_ENABLED": "false",
            "OLLAMA_BASE_URL": "invalid",
            "OLLAMA_MODEL": "",
            "MEDIA_ALIAS_LLM_PROMPT": "",
        }
    ) as bot:
        await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/test", None, "test"),))
        await set_functional_administrator(user_id=42)

        result = await _synchronize(bot)

        assert_that(result["payload"]["text"], contains_string("Поставлено заданий на алиасы: <b>0</b>"))
        assert_that(await enrichment_database.jobs(), empty())
        assert_that(await fake_ollama_server.requests(), empty())
        assert_that(await fake_statsd_server.metrics(name="functional.media_alias_enrichment.claimed"), empty())


@pytest.mark.parametrize(
    "overrides",
    [
        {"OLLAMA_BASE_URL": ""},
        {"OLLAMA_MODEL": ""},
        {"MEDIA_ALIAS_LLM_PROMPT": ""},
        {"OLLAMA_BASE_URL": "http://localhost/api"},
        {"MEDIA_ALIAS_ENRICHMENT_CONCURRENCY": "0"},
        {"MEDIA_ALIAS_ENRICHMENT_LEASE_REFRESH_SECONDS": "30"},
    ],
)
async def test_invalid_enabled_config_fails_before_connecting_external_services(
    *,
    overrides: dict[str, str],
    start_enrichment_bot: Callable[..., AbstractAsyncContextManager[EnrichmentBot]],
    fake_ollama_server: FakeOllamaServer,
) -> None:
    async with start_enrichment_bot(overrides=overrides, wait_ready=False) as bot:
        assert await asyncio.wait_for(bot.process.wait(), timeout=10) != 0
        await bot.stop()

        assert_that(await bot.telegram.requests(), empty())
        assert_that(await fake_ollama_server.requests(), empty())
        logs = "".join(bot.logs)
        assert "Connecting to the database" not in logs
        assert "ValueError" in logs


async def test_shutdown_during_download_returns_job_to_retry_without_ollama_request(
    *,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_yandex_server: FakeYandexServer,
    fake_ollama_server: FakeOllamaServer,
) -> None:
    await fake_yandex_server.configure_download(name="image.png", content=_media_bytes("image/png"), block=True)

    try:
        await _synchronize(enrichment_bot)
        await fake_yandex_server.wait_for_download("image.png")
        await enrichment_bot.stop()

        jobs = await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == "retry")
        assert_that(jobs, contains_exactly(has_entries(result_class="cancelled", lease_token=None, attempt_count=1)))
        assert_that(await fake_ollama_server.requests(), empty())
    finally:
        await fake_yandex_server.release_download("image.png")


@pytest.mark.parametrize(("retry_count", "expected_status", "request_count"), [(1, "succeeded", 2), (2, "failed", 1)])
async def test_expired_lease_cannot_be_refreshed_and_recovery_respects_retry_budget(
    *,
    retry_count: int,
    expected_status: str,
    request_count: int,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_ollama_server: FakeOllamaServer,
    read_functional_media_search_aliases: Callable[[str], Awaitable[tuple[str, ...] | None]],
) -> None:
    await fake_ollama_server.configure(
        block=True,
        responses=[
            {"content": '{"aliases":["Old result"]}'},
            {"content": '{"aliases":["Recovered result"]}'},
        ],
    )

    await _synchronize(enrichment_bot)
    await fake_ollama_server.requests(wait_for=1)

    await enrichment_database.connection.execute(
        """
        UPDATE media_alias_enrichment_jobs SET leased_until=now()-interval '1 second',
            attempt_count=$1, retry_count=$1
        """,
        retry_count,
    )

    if request_count == 2:
        await fake_ollama_server.requests(wait_for=2)
    else:
        await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == "failed")

    await fake_ollama_server.release()
    jobs = await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == expected_status)
    assert jobs[0]["attempt_count"] == retry_count + 1
    assert jobs[0]["retry_count"] == retry_count + 1
    assert_that(await fake_ollama_server.requests(), has_length(request_count))

    aliases = await read_functional_media_search_aliases("test/image.png")
    if expected_status == "succeeded":
        assert_that(aliases, contains_exactly("Recovered result"))
    else:
        assert jobs[0]["result_class"] == "attempts_exhausted"
        assert_that(aliases, empty())


async def test_partial_catalog_sync_enqueues_successful_category_and_recovered_category_on_next_sync(
    *,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    fake_yandex_server: FakeYandexServer,
    fake_ollama_server: FakeOllamaServer,
) -> None:
    await seed_functional_subscription_types(
        (FunctionalSubscriptionType(2, "/hidden", None, "hidden", is_active=False),)
    )
    await fake_yandex_server.configure_directory("hidden", fail=True)

    partial = await _synchronize(enrichment_bot)
    assert_that(partial["payload"]["text"], contains_string("завершена частично"))
    assert_that(partial["payload"]["text"], contains_string("Поставлено заданий на алиасы: <b>1</b>"))
    await enrichment_database.wait_for_jobs(lambda jobs: len(jobs) == 1 and jobs[0]["status"] == "succeeded")

    await fake_yandex_server.configure_directory("hidden", images=[{"name": "image.png"}])

    recovered = await _synchronize(enrichment_bot, message_id=101)
    assert_that(recovered["payload"]["text"], contains_string("Категорий с ошибками: <b>0</b>"))
    assert_that(recovered["payload"]["text"], contains_string("Поставлено заданий на алиасы: <b>1</b>"))
    jobs = await enrichment_database.wait_for_jobs(
        lambda jobs: len(jobs) == 2 and all(job["status"] == "succeeded" for job in jobs)
    )

    assert_that(
        jobs,
        contains_exactly(
            has_entries(source_path="hidden/image.png", attempt_count=1),
            has_entries(source_path="test/image.png", attempt_count=1),
        ),
    )
    assert_that(await fake_ollama_server.requests(), has_length(2))


async def test_heartbeat_waiting_for_row_lock_does_not_resurrect_expired_lease(
    *,
    enrichment_category: None,
    enrichment_bot: EnrichmentBot,
    enrichment_database: EnrichmentDatabase,
    fake_ollama_server: FakeOllamaServer,
    read_functional_media_search_aliases: Callable[[str], Awaitable[tuple[str, ...] | None]],
) -> None:
    await fake_ollama_server.configure(
        block=True,
        responses=[
            {"content": '{"aliases":["Old result"]}'},
            {"content": '{"aliases":["Recovered result"]}'},
        ],
    )
    await _synchronize(enrichment_bot)
    await fake_ollama_server.requests(wait_for=1)
    first = (await enrichment_database.jobs())[0]
    connection = enrichment_database.connection

    async with connection.transaction():
        await connection.execute("SELECT id FROM media_alias_enrichment_jobs WHERE id=$1 FOR UPDATE", first["id"])

        # Observe the real heartbeat blocked on our lock, not an assumed timer delay.
        async with asyncio.timeout(10):
            while not await connection.fetchval(
                "SELECT EXISTS(SELECT FROM pg_locks WHERE $1 = ANY(pg_blocking_pids(pid)))",
                connection.get_server_pid(),
            ):
                continue

        await connection.execute(
            "UPDATE media_alias_enrichment_jobs SET leased_until=$2 WHERE id=$1",
            first["id"],
            datetime.now(UTC),
        )

    await fake_ollama_server.requests(wait_for=2)
    jobs = await enrichment_database.jobs()
    assert jobs[0]["lease_token"] != first["lease_token"]

    await fake_ollama_server.release()
    jobs = await enrichment_database.wait_for_jobs(lambda jobs: jobs[0]["status"] == "succeeded")

    assert jobs[0]["attempt_count"] == 2
    assert_that(await read_functional_media_search_aliases("test/image.png"), contains_exactly("Recovered result"))

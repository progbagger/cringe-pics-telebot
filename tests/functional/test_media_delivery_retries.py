import asyncio
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

import aiohttp
import asyncpg
import pytest
from hamcrest import assert_that, empty, equal_to, has_entries, has_length

from tests.functional.conftest import (
    DependencyPorts,
    FakeYandexServer,
    FunctionalBotStarter,
    FunctionalSubscriptionType,
    MainKeyboardBot,
    _create_postgres_connection,
    _wait_until_ready,
)

MEDIA_FAILURE = {"error_code": 400, "description": "Bad Request: failed to get HTTP URL content"}


@dataclass(slots=True)
class Delivery:
    bot: MainKeyboardBot
    database: asyncpg.Connection
    scheduled: bool
    media_type: str
    lease_exists: Callable[[str], Awaitable[bool]]
    yandex: FakeYandexServer

    @property
    def method(self) -> str:
        return (
            {"photo": "sendPhoto", "animation": "sendAnimation", "video": "sendVideo"}[self.media_type]
            if self.scheduled
            else "editMessageMedia"
        )

    async def errors(self, *responses: dict[str, Any]) -> None:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.bot.telegram.base_url}/test/media-errors", json={"responses": responses}) as response,
        ):
            response.raise_for_status()

    async def fail_urls(self, *names: str) -> None:
        async with aiohttp.ClientSession() as session:
            for name in names:
                async with session.post(
                    f"{self.yandex.base_url}/test/download", json={"name": name, "url_status": 503}
                ) as response:
                    response.raise_for_status()

    async def start(self) -> None:
        if self.scheduled:
            await self.bot.advance(wall_now=datetime(2030, 9, 19, 3, tzinfo=UTC))
        else:
            await self.bot.telegram.push_message(text="/rotation")

    async def requests(self) -> list[dict[str, Any]]:
        return await self.bot.telegram.requests(method=self.method)

    async def media_ids(self) -> list[str]:
        return [
            r["payload"][self.media_type] if self.scheduled else r["payload"]["media"]["media"]
            for r in await self.requests()
        ]

    async def shown(self) -> list[str]:
        rows = await self.database.fetch(
            """SELECT media.source_path FROM user_media_cycle_entries AS entry
            JOIN category_media AS media ON media.id = entry.media_id
            WHERE entry.user_id = 42 AND entry.reservation_token IS NULL ORDER BY media.source_path"""
        )
        return [r["source_path"] for r in rows]

    async def successful(self, count: int = 1) -> None:
        async def check() -> None:
            assert len(await self.shown()) == count

        await _wait_until_ready(check, "successful rotation confirmation")

    async def failed(self) -> None:
        await self.bot.wait_for_log(
            "Failed to send scheduled image" if self.scheduled else "Failed to send media to user"
        )
        if not self.scheduled:
            await self.bot.telegram.wait_for_request("editMessageText")
        assert_that(await self.shown(), empty())

    async def cleaned_up(self) -> None:
        assert (
            await self.database.fetchval(
                "SELECT count(*) FROM user_media_cycle_entries WHERE reservation_token IS NOT NULL"
            )
            == 0
        )
        for row in await self.database.fetch("SELECT id, source_revision FROM category_media"):
            assert not await self.lease_exists(f"media-materialization:{row['id']}:{row['source_revision']}")
        assert await self.database.fetchval("SELECT bool_and(is_active) FROM category_media")


@pytest.fixture
async def delivery_database(docker_compose: DependencyPorts) -> AsyncIterator[asyncpg.Connection]:
    connection = await _create_postgres_connection(docker_compose)
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def start_delivery(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    fake_yandex_server: FakeYandexServer,
    create_functional_user: Callable[..., Awaitable[None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    start_main_keyboard_bot: Callable[..., AbstractAsyncContextManager[MainKeyboardBot]],
    delivery_database: asyncpg.Connection,
    redis_key_exists: Callable[[str], Awaitable[bool]],
) -> Callable[..., AbstractAsyncContextManager[Delivery]]:
    @asynccontextmanager
    async def start(
        *,
        names: tuple[str, ...] = ("first.png", "second.png"),
        mime_type: str = "image/png",
        media_type: str = "photo",
        scheduled: bool = False,
        retries: str | None = None,
    ) -> AsyncIterator[Delivery]:
        await reset_dependency_state(
            (FunctionalSubscriptionType(1, "/rotation", time(10) if scheduled else None, "rotation"),)
        )
        await create_functional_user(user_id=42)
        if scheduled:
            await create_user_subscription(user_id=42, subscription_type_id=1, timezone_offset_minutes=420)
        await fake_yandex_server.configure_directory(
            "rotation", images=[{"name": name, "mime_type": mime_type} for name in names]
        )
        async with start_main_keyboard_bot(media_delivery_max_retries=retries) as bot:

            async def ready() -> None:
                assert await delivery_database.fetchval("SELECT count(*) FROM category_media") == len(names)

            await _wait_until_ready(ready, "rotation media catalog")
            await bot.telegram.reset()
            yield Delivery(bot, delivery_database, scheduled, media_type, redis_key_exists, fake_yandex_server)

    return start


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize(
    "suffix,mime_type,media_type",
    [("png", "image/png", "photo"), ("gif", "image/gif", "animation"), ("mp4", "video/mp4", "video")],
)
async def test_rejected_cold_media_rotates_once_and_confirms_only_success(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
    scheduled: bool,
    suffix: str,
    mime_type: str,
    media_type: str,
) -> None:
    async with start_delivery(
        names=(f"first.{suffix}", f"second.{suffix}"), mime_type=mime_type, media_type=media_type, scheduled=scheduled
    ) as delivery:
        await delivery.errors(MEDIA_FAILURE)
        await delivery.start()
        await delivery.successful()
        sent = await delivery.media_ids()
        assert len(sent) == len(set(sent)) == 2
        assert (await delivery.shown())[0].endswith(sent[-1].rsplit("/", 1)[-1])
        await delivery.cleaned_up()
        assert_that(await delivery.bot.telegram.requests(method="editMessageText"), empty())
        if scheduled:
            assert (await delivery.requests())[-1]["payload"]["reply_markup"]["selective"] is False
            assert_that(await delivery.bot.telegram.requests(method="sendMessage"), empty())
        else:
            placeholders = await delivery.bot.telegram.requests(method="sendMessage")
            assert_that(placeholders, has_length(1))
            assert_that(placeholders[0]["payload"], has_entries(text="<i>Выбираю картинку</i>"))
            assert "reply_markup" not in placeholders[0]["payload"]
            assert len({r["payload"]["message_id"] for r in await delivery.requests()}) == 1
        log = await delivery.bot.wait_for_log("Media candidate failed")
        assert "attempt=1 attempt_limit=4" in log
        assert "error_type=TelegramBadRequest" in log
        assert "http" not in log and "functional-test-token" not in log


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("retries,candidate_count,expected", [(None, 6, 4), ("0", 3, 1), ("1", 4, 2), ("6", 2, 2)])
async def test_all_rejections_stop_at_retry_budget_or_distinct_candidate_exhaustion(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
    scheduled: bool,
    retries: str | None,
    candidate_count: int,
    expected: int,
) -> None:
    async with start_delivery(
        names=tuple(f"{i}.png" for i in range(candidate_count)), retries=retries, scheduled=scheduled
    ) as delivery:
        await delivery.errors(*([MEDIA_FAILURE] * 10))
        await delivery.start()
        await delivery.failed()
        sent = await delivery.media_ids()
        assert len(sent) == len(set(sent)) == expected
        await delivery.cleaned_up()
        if not scheduled:
            assert_that(await delivery.bot.telegram.requests(method="editMessageText"), has_length(1))


@pytest.mark.parametrize(
    "error",
    [
        {"error_code": 400, "description": "Bad Request: chat not found"},
        {"error_code": 400, "description": "Bad Request: message media can't be edited"},
        {"error_code": 403, "description": "Forbidden: bot was blocked by the user"},
        {"error_code": 429, "description": "Too Many Requests", "retry_after": 1},
        {"disconnect": True},
    ],
)
async def test_non_media_errors_and_uncertain_network_result_do_not_rotate(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
    error: dict[str, Any],
) -> None:
    async with start_delivery() as delivery:
        await delivery.errors(error)
        await delivery.start()
        await delivery.failed()
        assert_that(await delivery.requests(), has_length(1))
        await delivery.cleaned_up()


@pytest.mark.parametrize("scheduled", [False, True])
async def test_storage_url_failure_rotates_from_pending_to_ready_without_sending_failed_file(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
    scheduled: bool,
) -> None:
    async with start_delivery(names=("unavailable.png", "ready.png"), scheduled=scheduled) as delivery:
        await delivery.fail_urls("unavailable.png")
        await delivery.database.execute(
            """UPDATE category_media SET telegram_file_id = 'ready-id',
                telegram_file_unique_id = 'unique', materialized_at = now() WHERE name = 'ready.png'"""
        )
        await delivery.start()
        await delivery.successful()
        assert_that(await delivery.media_ids(), equal_to(["ready-id"]))
        assert_that(await delivery.shown(), equal_to(["rotation/ready.png"]))
        await delivery.cleaned_up()
        assert "error_type=MediaDownloadUrlError" in await delivery.bot.wait_for_log("Media candidate failed")


async def test_all_storage_url_failures_exhaust_distinct_candidates_without_telegram_sends(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
) -> None:
    async with start_delivery(names=("first.png", "second.png")) as delivery:
        await delivery.fail_urls("first.png", "second.png")
        await delivery.start()
        await delivery.failed()
        assert_that(await delivery.requests(), empty())
        await delivery.cleaned_up()
        assert sum("Media candidate failed" in line for line in delivery.bot.logs) == 2


async def test_invalid_cached_file_id_recovery_does_not_consume_candidate_rotation_budget(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
) -> None:
    async with start_delivery(retries="1") as delivery:
        await delivery.database.execute(
            """UPDATE category_media SET telegram_file_id = 'invalid-' || id,
                telegram_file_unique_id = 'unique-' || id, materialized_at = now()"""
        )
        invalid_ids = [
            r["telegram_file_id"] for r in await delivery.database.fetch("SELECT telegram_file_id FROM category_media")
        ]
        await delivery.bot.telegram.set_invalid_file_ids(*invalid_ids)
        await delivery.errors({}, MEDIA_FAILURE)
        await delivery.start()
        await delivery.successful()
        sent = await delivery.media_ids()
        assert len(sent) == 4  # cached rejection + URL rejection, another cached rejection + successful URL
        assert sent[0] != sent[2]
        await delivery.cleaned_up()


async def test_excluded_candidate_stays_excluded_after_cycle_reset(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
) -> None:
    async with start_delivery() as delivery:
        await delivery.database.execute("""
            INSERT INTO user_media_cycle_states(user_id, subscription_type_id, last_media_id, updated_at)
            SELECT 42, 1, id, now() FROM category_media WHERE name = 'second.png'""")
        await delivery.database.execute("""
            INSERT INTO user_media_cycle_entries(user_id, subscription_type_id, media_id, shown_at)
            SELECT 42, 1, id, now() FROM category_media WHERE name = 'second.png'""")
        await delivery.errors(MEDIA_FAILURE)
        await delivery.start()
        await delivery.bot.telegram.wait_for_requests(delivery.method, count=2)
        await delivery.successful()
        sent = await delivery.media_ids()
        assert sent[0].endswith("first.png") and sent[1].endswith("second.png")
        await delivery.cleaned_up()


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("failure", ["response_type", "materialization", "confirmation"])
async def test_successful_send_never_rotates_on_post_send_failure(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
    scheduled: bool,
    failure: str,
) -> None:
    async with start_delivery(scheduled=scheduled) as delivery:
        if failure == "response_type":
            await delivery.bot.telegram.set_media_response_types({delivery.method: "document"})
        else:
            condition = (
                "NEW.telegram_file_id IS NOT NULL" if failure == "materialization" else "NEW.reservation_token IS NULL"
            )
            table = "category_media" if failure == "materialization" else "user_media_cycle_entries"
            await delivery.database.execute(f"""
                CREATE FUNCTION functional_fail_post_send() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF {condition} THEN RAISE EXCEPTION 'post send failure'; END IF; RETURN NEW; END $$;
                CREATE TRIGGER functional_fail_post_send BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION functional_fail_post_send();""")
        try:
            await delivery.start()
            if failure == "confirmation":
                await delivery.bot.wait_for_log(
                    "Failed to send scheduled image" if scheduled else "Failed to send media to user"
                )
            else:
                await delivery.successful()
                await delivery.bot.wait_for_log("Failed to materialize delivered media")
            assert_that(await delivery.requests(), has_length(1))
            if failure != "confirmation":
                await delivery.cleaned_up()
        finally:
            if failure != "response_type":
                await delivery.database.execute(
                    f"DROP TRIGGER functional_fail_post_send ON {table}; DROP FUNCTION functional_fail_post_send()"
                )


async def test_scheduler_cancellation_releases_candidate_without_rotation(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
) -> None:
    async with start_delivery(scheduled=True) as delivery:
        await delivery.bot.telegram.block_method(delivery.method)
        await delivery.start()
        await delivery.bot.telegram.wait_for_request(delivery.method)
        delivery.bot.process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(delivery.bot.process.wait(), timeout=10)
        await delivery.cleaned_up()
        assert_that(await delivery.requests(), has_length(1))


@pytest.mark.parametrize("value", ["-1", "invalid", "1.5"])
async def test_invalid_delivery_retry_setting_fails_at_startup(
    start_functional_bot_process: FunctionalBotStarter,
    value: str,
) -> None:
    async with start_functional_bot_process(
        media_delivery_max_retries=value, wait_for_telegram=False, capture_output=True
    ) as bot:
        await asyncio.wait_for(bot.process.wait(), timeout=10)
        assert bot.process.stdout is not None
        output = (await bot.process.stdout.read()).decode()
        assert bot.process.returncode != 0
        assert "MEDIA_DELIVERY_MAX_RETRIES must be a non-negative integer" in output
        assert_that(await bot.telegram.requests(method="getMe"), empty())


async def test_rotation_does_not_reset_another_delivery_reservation(
    start_delivery: Callable[..., AbstractAsyncContextManager[Delivery]],
) -> None:
    async with start_delivery() as delivery:
        await delivery.database.execute("""
            INSERT INTO user_media_cycle_states(user_id, subscription_type_id, updated_at)
            VALUES (42, 1, now());
            INSERT INTO user_media_cycle_entries(
                user_id, subscription_type_id, media_id, reservation_token, reserved_until
            ) SELECT 42, 1, id, 'parallel-send', now() + interval '5 minutes'
            FROM category_media WHERE name = 'second.png';
        """)
        await delivery.errors(MEDIA_FAILURE)
        await delivery.start()
        await delivery.failed()
        assert_that(await delivery.requests(), has_length(1))
        rows = await delivery.database.fetch("SELECT reservation_token FROM user_media_cycle_entries")
        assert_that([r["reservation_token"] for r in rows], equal_to(["parallel-send"]))
        await delivery.database.execute(
            "DELETE FROM user_media_cycle_entries WHERE reservation_token = 'parallel-send'"
        )
        await delivery.cleaned_up()

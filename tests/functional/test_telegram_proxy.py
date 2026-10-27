import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from hamcrest import assert_that, contains_string, has_item, only_contains

from tests.functional.conftest import (
    FakeHTTPProxyServer,
    FakeYandexServer,
    FunctionalBotStarter,
    FunctionalSubscriptionType,
)


async def test_telegram_requests_use_dedicated_proxy_while_yandex_stays_direct(
    start_functional_bot_process: FunctionalBotStarter,
    fake_http_proxy_server: FakeHTTPProxyServer,
    fake_yandex_server: FakeYandexServer,
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state((FunctionalSubscriptionType(1, "/day", None, "day"),))

    async with start_functional_bot_process(telegram_proxy_url=fake_http_proxy_server.proxy_url) as bot:
        await fake_yandex_server.wait_for_request("resources")
        await fake_http_proxy_server.wait_for_targets()

        targets = await fake_http_proxy_server.targets()

        telegram_target = urlsplit(bot.telegram.base_url).netloc
        yandex_target = urlsplit(fake_yandex_server.base_url).netloc
        assert_that(targets, has_item(telegram_target))
        assert_that(targets, only_contains(telegram_target))
        assert yandex_target not in targets


async def test_invalid_telegram_proxy_fails_without_exposing_credentials(
    start_functional_bot_process: FunctionalBotStarter,
) -> None:
    proxy_url = "invalid://proxy-user:super-secret@127.0.0.1:1080"

    async with start_functional_bot_process(
        telegram_proxy_url=proxy_url,
        wait_for_telegram=False,
        capture_output=True,
    ) as bot:
        await asyncio.wait_for(bot.process.wait(), timeout=10)
        assert bot.process.stdout is not None
        output = (await bot.process.stdout.read()).decode("utf-8", errors="replace")

    assert bot.process.returncode != 0
    assert_that(output, contains_string("Invalid TELEGRAM_PROXY_URL"))
    assert "proxy-user" not in output
    assert "super-secret" not in output

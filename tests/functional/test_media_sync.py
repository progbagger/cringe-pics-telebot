from collections.abc import Awaitable, Callable
from datetime import time, timedelta

import pytest

from cringe_pics_telebot.repositories import redis as cache
from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaStatus,
    get_category_media_by_subscription_types,
)
from cringe_pics_telebot.repositories.postgres import (
    connect as connect_postgres,
)
from cringe_pics_telebot.repositories.redis import connect as connect_redis
from cringe_pics_telebot.repositories.yandex import connect as connect_yandex
from cringe_pics_telebot.services.media_sync import MEDIA_SYNC_LEASE_KEY, synchronize_media_catalog
from tests.functional.conftest import (
    BOT_ENV,
    POSTGRES_ENV,
    REDIS_ENV,
    DependencyPorts,
    FakeYandexServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state(
        (
            FunctionalSubscriptionType(1, "/day", time(13), "day"),
            FunctionalSubscriptionType(2, "/broken", time(14), "broken"),
        )
    )


async def test_sync_reconciles_changes_isolates_failures_and_honors_lease(
    docker_compose: DependencyPorts,
    fake_yandex_server: FakeYandexServer,
) -> None:
    connect_yandex(
        BOT_ENV["YANDEX_DISK_TOKEN"],
        api_base_url=f"{fake_yandex_server.base_url}/v1/disk/",
    )
    async with (
        connect_postgres(
            username=POSTGRES_ENV["POSTGRES_USER"],
            password=POSTGRES_ENV["POSTGRES_PASSWORD"],
            database=POSTGRES_ENV["POSTGRES_DB"],
            port=docker_compose.postgres,
            host=POSTGRES_ENV["POSTGRES_HOST"],
        ),
        connect_redis(
            username=REDIS_ENV["REDIS_USERNAME"],
            password=REDIS_ENV["REDIS_PASSWORD"],
            port=docker_compose.redis,
            host=REDIS_ENV["REDIS_HOST"],
        ),
    ):
        first = await synchronize_media_catalog()
        assert first.categories == 2
        assert first.failed == 0
        assert first.discovered == first.created == 3

        await fake_yandex_server.configure_directory(
            "day",
            images=[
                {"name": "image.png", "sha256": "changed"},
                {"name": "third.png", "sha256": "new"},
            ],
        )
        await fake_yandex_server.configure_directory("broken", fail=True)

        second = await synchronize_media_catalog()
        assert second.categories == 1
        assert second.failed == 1
        assert second.discovered == 2
        assert second.created == second.changed == second.deactivated == 1

        media = await get_category_media_by_subscription_types([1, 2], active_only=False)
        by_path = {item.source_path: item for item in media}
        assert by_path["day/image.png"].source_revision == "sha256:changed"
        assert by_path["day/image.png"].status is CategoryMediaStatus.pending
        assert by_path["day/second.png"].status is CategoryMediaStatus.inactive
        assert by_path["day/third.png"].status is CategoryMediaStatus.pending
        assert by_path["broken/image.png"].is_active is True

        await cache.set(
            key=MEDIA_SYNC_LEASE_KEY,
            value="another-instance",
            cls=str,
            ttl=timedelta(minutes=1),
        )
        requests_before_skip = len(await fake_yandex_server.requests())
        skipped = await synchronize_media_catalog()
        assert skipped.acquired is False
        assert len(await fake_yandex_server.requests()) == requests_before_skip

    requests = await fake_yandex_server.requests()
    assert not any(request["method"] in {"resources/download", "download"} for request in requests)

from collections.abc import Awaitable, Callable
from datetime import time, timedelta

import pytest
from hamcrest import assert_that, empty, equal_to, is_, same_instance

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
from cringe_pics_telebot.services.media_sync import MEDIA_SYNC_LEASE_KEY, MediaSyncSummary, synchronize_media_catalog
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


async def test_sync_includes_inactive_categories(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    fake_yandex_server: FakeYandexServer,
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await reset_dependency_state((FunctionalSubscriptionType(1, "/inactive", time(13), "inactive", is_active=False),))
    await fake_yandex_server.configure_directory("inactive", images=[{"name": "inactive.png"}])

    summary = await synchronize_functional_media_catalog()

    assert_that(summary.categories, equal_to(1))
    assert_that((summary.discovered, summary.created), equal_to((1, 1)))
    assert_that(
        [
            request["params"]["path"]
            for request in await fake_yandex_server.requests()
            if request["method"] == "resources"
        ],
        equal_to(["app:/inactive"]),
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
        assert_that(first.categories, equal_to(2))
        assert_that(first.failed, equal_to(0))
        assert_that((first.discovered, first.created), equal_to((3, 3)))

        await fake_yandex_server.configure_directory(
            "day",
            images=[
                {"name": "image.png", "sha256": "changed"},
                {"name": "third.png", "sha256": "new"},
            ],
        )
        await fake_yandex_server.configure_directory("broken", fail=True)

        second = await synchronize_media_catalog()
        assert_that(second.categories, equal_to(1))
        assert_that(second.failed, equal_to(1))
        assert_that(second.discovered, equal_to(2))
        assert_that((second.created, second.changed, second.deactivated), equal_to((1, 1, 1)))

        media = await get_category_media_by_subscription_types([1, 2], active_only=False)
        by_path = {item.source_path: item for item in media}
        assert_that(by_path["day/image.png"].source_revision, equal_to("sha256:changed"))
        assert_that(by_path["day/image.png"].status, same_instance(CategoryMediaStatus.pending))
        assert_that(by_path["day/second.png"].status, same_instance(CategoryMediaStatus.inactive))
        assert_that(by_path["day/third.png"].status, same_instance(CategoryMediaStatus.pending))
        assert_that(by_path["broken/image.png"].is_active, is_(True))

        await cache.set(
            key=MEDIA_SYNC_LEASE_KEY,
            value="another-instance",
            cls=str,
            ttl=timedelta(minutes=1),
        )
        requests_before_skip = len(await fake_yandex_server.requests())
        skipped = await synchronize_media_catalog()
        assert_that(skipped.acquired, is_(False))
        assert_that(len(await fake_yandex_server.requests()), equal_to(requests_before_skip))

    requests = await fake_yandex_server.requests()
    assert_that(
        {request["method"] for request in requests} & {"resources/download", "download"},
        empty(),
    )

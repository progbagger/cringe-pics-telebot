from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time

import pytest
from hamcrest import assert_that, empty, equal_to, none, not_none, same_instance

from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaSource,
    CategoryMediaStatus,
    TelegramMediaType,
    connect,
    get_category_media,
    get_category_media_by_subscription_types,
    invalidate_category_media_file_id,
    materialize_category_media,
    transaction,
)
from cringe_pics_telebot.services.media_catalog import reconcile_category_media_snapshot
from tests.functional.conftest import (
    POSTGRES_ENV,
    DependencyPorts,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state((FunctionalSubscriptionType(1, "/day", time(13, 0), "day"),))


async def test_reconcile_materialize_and_replace_revision(docker_compose: DependencyPorts) -> None:
    source = _source("day/image.png", revision="sha256:first")
    first_seen = datetime(2026, 8, 19, 1, tzinfo=UTC)
    materialized_at = datetime(2026, 8, 19, 2, tzinfo=UTC)
    second_seen = datetime(2026, 8, 19, 3, tzinfo=UTC)

    async with _connect(docker_compose):
        result = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[source],
            seen_at=first_seen,
        )
        assert_that((result.discovered, result.created), equal_to((1, 1)))
        assert_that(
            (result.changed, result.reactivated, result.deactivated, result.unchanged),
            equal_to((0, 0, 0, 0)),
        )

        media, *_ = await get_category_media_by_subscription_types([1])
        assert_that(media.status, same_instance(CategoryMediaStatus.pending))
        assert_that(media.last_seen_at, equal_to(first_seen))

        async with transaction():
            materialized = await materialize_category_media(
                media_id=media.id,
                source_revision=source.source_revision,
                telegram_file_id="telegram-file-id",
                telegram_file_unique_id="telegram-unique-id",
                materialized_at=materialized_at,
            )
        assert_that(
            [item.status for item in [materialized] if item is not None],
            equal_to([CategoryMediaStatus.ready]),
        )

        unchanged = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[source],
            seen_at=second_seen,
        )
        assert_that(unchanged.unchanged, equal_to(1))
        ready, *_ = await get_category_media_by_subscription_types([1], ready_only=True)
        assert_that(ready.telegram_file_id, equal_to("telegram-file-id"))
        assert_that(ready.last_seen_at, equal_to(second_seen))
        assert_that(ready.updated_at, equal_to(materialized_at))

        changed = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[_source(source.source_path, revision="sha256:second")],
            seen_at=datetime(2026, 8, 19, 4, tzinfo=UTC),
        )
        assert_that(changed.changed, equal_to(1))
        pending = await get_category_media(media.id)
        assert_that(
            [
                (item.status, item.telegram_file_id, item.telegram_file_unique_id, item.materialized_at)
                for item in [pending]
                if item is not None
            ],
            equal_to([(CategoryMediaStatus.pending, None, None, None)]),
        )


async def test_deactivate_and_reactivate_same_revision_preserves_file_id(
    docker_compose: DependencyPorts,
) -> None:
    source = _source("day/image.png", revision="sha256:first")

    async with _connect(docker_compose):
        await reconcile_category_media_snapshot(subscription_type_id=1, sources=[source])
        media, *_ = await get_category_media_by_subscription_types([1])
        async with transaction():
            assert_that(
                await materialize_category_media(
                    media_id=media.id,
                    source_revision=source.source_revision,
                    telegram_file_id="telegram-file-id",
                    telegram_file_unique_id="telegram-unique-id",
                ),
                not_none(),
            )

        removed = await reconcile_category_media_snapshot(subscription_type_id=1, sources=[])
        assert_that(removed.deactivated, equal_to(1))
        assert_that(await get_category_media_by_subscription_types([1]), empty())
        inactive = await get_category_media(media.id)
        assert_that(
            [(item.status, item.telegram_file_id) for item in [inactive] if item is not None],
            equal_to([(CategoryMediaStatus.inactive, "telegram-file-id")]),
        )

        restored = await reconcile_category_media_snapshot(subscription_type_id=1, sources=[source])
        assert_that(restored.reactivated, equal_to(1))
        ready, *_ = await get_category_media_by_subscription_types([1], ready_only=True)
        assert_that(ready.id, equal_to(media.id))
        assert_that(ready.status, same_instance(CategoryMediaStatus.ready))
        assert_that(ready.telegram_file_id, equal_to("telegram-file-id"))


async def test_materialize_and_invalidate_are_conditional(docker_compose: DependencyPorts) -> None:
    source = _source("day/image.png", revision="sha256:first")

    async with _connect(docker_compose):
        await reconcile_category_media_snapshot(subscription_type_id=1, sources=[source])
        media, *_ = await get_category_media_by_subscription_types([1])

        async with transaction():
            assert_that(
                await materialize_category_media(
                    media_id=media.id,
                    source_revision="sha256:stale",
                    telegram_file_id="telegram-file-id",
                    telegram_file_unique_id="telegram-unique-id",
                ),
                none(),
            )
        async with transaction():
            assert_that(
                await materialize_category_media(
                    media_id=media.id,
                    source_revision=source.source_revision,
                    telegram_file_id="telegram-file-id",
                    telegram_file_unique_id="telegram-unique-id",
                ),
                not_none(),
            )
        async with transaction():
            assert_that(
                await invalidate_category_media_file_id(
                    media_id=media.id,
                    telegram_file_id="stale-file-id",
                ),
                none(),
            )

        async with transaction():
            invalidated = await invalidate_category_media_file_id(
                media_id=media.id,
                telegram_file_id="telegram-file-id",
            )
        assert_that(
            [item.status for item in [invalidated] if item is not None],
            equal_to([CategoryMediaStatus.pending]),
        )


async def test_reconcile_deduplicates_source_paths(docker_compose: DependencyPorts) -> None:
    first = _source("day/image.png", revision="sha256:first")
    second = _source("day/image.png", revision="sha256:second")
    async with _connect(docker_compose):
        result = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[first, second],
        )
        assert_that((result.discovered, result.created), equal_to((1, 1)))
        media, *_ = await get_category_media_by_subscription_types([1])
        assert_that(media.source_revision, equal_to(second.source_revision))


def _source(path: str, *, revision: str) -> CategoryMediaSource:
    return CategoryMediaSource(
        source_path=path,
        source_revision=revision,
        name=path.rsplit("/", maxsplit=1)[-1],
        mime_type="image/png",
        telegram_media_type=TelegramMediaType.photo,
    )


def _connect(docker_compose: DependencyPorts):
    return connect(
        username=POSTGRES_ENV["POSTGRES_USER"],
        password=POSTGRES_ENV["POSTGRES_PASSWORD"],
        database=POSTGRES_ENV["POSTGRES_DB"],
        port=docker_compose.postgres,
        host=POSTGRES_ENV["POSTGRES_HOST"],
    )

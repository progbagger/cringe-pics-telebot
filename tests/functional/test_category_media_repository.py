from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time

import pytest

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
        assert result.discovered == result.created == 1
        assert result.changed == result.reactivated == result.deactivated == result.unchanged == 0

        media, *_ = await get_category_media_by_subscription_types([1])
        assert media.status is CategoryMediaStatus.pending
        assert media.last_seen_at == first_seen

        async with transaction():
            materialized = await materialize_category_media(
                media_id=media.id,
                source_revision=source.source_revision,
                telegram_file_id="telegram-file-id",
                telegram_file_unique_id="telegram-unique-id",
                materialized_at=materialized_at,
            )
        assert materialized is not None
        assert materialized.status is CategoryMediaStatus.ready

        unchanged = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[source],
            seen_at=second_seen,
        )
        assert unchanged.unchanged == 1
        ready, *_ = await get_category_media_by_subscription_types([1], ready_only=True)
        assert ready.telegram_file_id == "telegram-file-id"
        assert ready.last_seen_at == second_seen
        assert ready.updated_at == materialized_at

        changed = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[_source(source.source_path, revision="sha256:second")],
            seen_at=datetime(2026, 8, 19, 4, tzinfo=UTC),
        )
        assert changed.changed == 1
        pending = await get_category_media(media.id)
        assert pending is not None
        assert pending.status is CategoryMediaStatus.pending
        assert pending.telegram_file_id is None
        assert pending.telegram_file_unique_id is None
        assert pending.materialized_at is None


async def test_deactivate_and_reactivate_same_revision_preserves_file_id(
    docker_compose: DependencyPorts,
) -> None:
    source = _source("day/image.png", revision="sha256:first")

    async with _connect(docker_compose):
        await reconcile_category_media_snapshot(subscription_type_id=1, sources=[source])
        media, *_ = await get_category_media_by_subscription_types([1])
        async with transaction():
            assert await materialize_category_media(
                media_id=media.id,
                source_revision=source.source_revision,
                telegram_file_id="telegram-file-id",
                telegram_file_unique_id="telegram-unique-id",
            )

        removed = await reconcile_category_media_snapshot(subscription_type_id=1, sources=[])
        assert removed.deactivated == 1
        assert await get_category_media_by_subscription_types([1]) == []
        inactive = await get_category_media(media.id)
        assert inactive is not None
        assert inactive.status is CategoryMediaStatus.inactive
        assert inactive.telegram_file_id == "telegram-file-id"

        restored = await reconcile_category_media_snapshot(subscription_type_id=1, sources=[source])
        assert restored.reactivated == 1
        ready, *_ = await get_category_media_by_subscription_types([1], ready_only=True)
        assert ready.id == media.id
        assert ready.status is CategoryMediaStatus.ready
        assert ready.telegram_file_id == "telegram-file-id"


async def test_materialize_and_invalidate_are_conditional(docker_compose: DependencyPorts) -> None:
    source = _source("day/image.png", revision="sha256:first")

    async with _connect(docker_compose):
        await reconcile_category_media_snapshot(subscription_type_id=1, sources=[source])
        media, *_ = await get_category_media_by_subscription_types([1])

        async with transaction():
            assert (
                await materialize_category_media(
                    media_id=media.id,
                    source_revision="sha256:stale",
                    telegram_file_id="telegram-file-id",
                    telegram_file_unique_id="telegram-unique-id",
                )
                is None
            )
        async with transaction():
            assert await materialize_category_media(
                media_id=media.id,
                source_revision=source.source_revision,
                telegram_file_id="telegram-file-id",
                telegram_file_unique_id="telegram-unique-id",
            )
        async with transaction():
            assert (
                await invalidate_category_media_file_id(
                    media_id=media.id,
                    telegram_file_id="stale-file-id",
                )
                is None
            )

        async with transaction():
            invalidated = await invalidate_category_media_file_id(
                media_id=media.id,
                telegram_file_id="telegram-file-id",
            )
        assert invalidated is not None
        assert invalidated.status is CategoryMediaStatus.pending


async def test_reconcile_deduplicates_source_paths(docker_compose: DependencyPorts) -> None:
    first = _source("day/image.png", revision="sha256:first")
    second = _source("day/image.png", revision="sha256:second")
    async with _connect(docker_compose):
        result = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[first, second],
        )
        assert result.discovered == result.created == 1
        media, *_ = await get_category_media_by_subscription_types([1])
        assert media.source_revision == second.source_revision


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

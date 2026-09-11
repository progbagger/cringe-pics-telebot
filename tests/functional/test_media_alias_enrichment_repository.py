from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

import pytest
from hamcrest import assert_that, empty, equal_to, has_properties, same_instance
from sqlalchemy import update

from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaSource,
    MediaAliasEnrichmentJobStatus,
    TelegramMediaType,
    claim_media_alias_enrichment_jobs,
    connect,
    get_category_media_by_subscription_types,
    get_media_alias_enrichment_jobs,
    materialize_category_media,
    refresh_media_alias_enrichment_job_lease,
    transaction,
)
from cringe_pics_telebot.repositories.postgres.connection import get_connection
from cringe_pics_telebot.repositories.postgres.tables import media_alias_enrichment_jobs
from cringe_pics_telebot.services.media_alias_enrichment_settings import MediaAliasEnrichmentSettings
from cringe_pics_telebot.services.media_catalog import reconcile_category_media_snapshot
from cringe_pics_telebot.services.media_search_aliases import parse_media_search_aliases, set_media_search_aliases
from tests.functional.conftest import POSTGRES_ENV, DependencyPorts, FunctionalSubscriptionType

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
SETTINGS = MediaAliasEnrichmentSettings(
    enabled=True,
    ollama_base_url="http://ollama:11434",
    ollama_model="gemma3:12b",
    prompt="Опиши изображение",
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state((FunctionalSubscriptionType(1, "/inactive", time(13), "inactive", is_active=False),))


async def test_reconcile_enqueues_active_pending_and_ready_media_without_aliases_and_reopens_terminal_jobs(
    docker_compose: DependencyPorts,
) -> None:
    sources = [_source("inactive/pending.png", "sha256:pending"), _source("inactive/ready.png", "sha256:ready")]
    manual_source = _source("inactive/manual.png", "sha256:manual")

    async with _connect(docker_compose):
        await reconcile_category_media_snapshot(subscription_type_id=1, sources=[*sources, manual_source], seen_at=NOW)
        media = await get_category_media_by_subscription_types([1])
        by_path = {item.source_path: item for item in media}
        async with transaction():
            materialized = await materialize_category_media(
                media_id=by_path["inactive/ready.png"].id,
                source_revision="sha256:ready",
                telegram_file_id="ready-file-id",
                telegram_file_unique_id="ready-unique-id",
                materialized_at=NOW,
            )
        assert materialized is not None
        await set_media_search_aliases(
            by_path["inactive/manual.png"].id,
            parse_media_search_aliases("ручной алиас"),
        )

        first = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[*sources, manual_source],
            seen_at=NOW + timedelta(minutes=1),
            alias_enrichment_settings=SETTINGS,
        )
        jobs = await get_media_alias_enrichment_jobs()

        assert_that(first.alias_enrichment_queued, equal_to(2))
        assert_that(
            [(job.media_id, job.source_revision, job.status) for job in jobs],
            equal_to(
                [
                    (
                        by_path["inactive/pending.png"].id,
                        "sha256:pending",
                        MediaAliasEnrichmentJobStatus.pending,
                    ),
                    (
                        by_path["inactive/ready.png"].id,
                        "sha256:ready",
                        MediaAliasEnrichmentJobStatus.pending,
                    ),
                ]
            ),
        )
        repeated = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[*sources, manual_source],
            seen_at=NOW + timedelta(minutes=2),
            alias_enrichment_settings=SETTINGS,
        )
        assert_that(repeated.alias_enrichment_queued, equal_to(0))

        async with transaction(), get_connection() as connection:
            await connection.execute(
                update(media_alias_enrichment_jobs)
                .where(media_alias_enrichment_jobs.c.id == jobs[0].id)
                .values(
                    status=MediaAliasEnrichmentJobStatus.failed,
                    attempt_count=3,
                    retry_count=3,
                    result_class="max_attempts",
                    finished_at=NOW,
                )
            )
            await connection.execute(
                update(media_alias_enrichment_jobs)
                .where(media_alias_enrichment_jobs.c.id == jobs[1].id)
                .values(
                    status=MediaAliasEnrichmentJobStatus.succeeded,
                    attempt_count=1,
                    retry_count=1,
                    result_class="aliases_saved",
                    finished_at=NOW,
                )
            )

        reopened = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[*sources, manual_source],
            seen_at=NOW + timedelta(minutes=3),
            alias_enrichment_settings=SETTINGS,
        )
        reopened_jobs = await get_media_alias_enrichment_jobs()

    assert_that(reopened.alias_enrichment_queued, equal_to(2))
    assert_that([job.status for job in reopened_jobs], equal_to([MediaAliasEnrichmentJobStatus.pending] * 2))
    assert_that([job.attempt_count for job in reopened_jobs], equal_to([3, 1]))
    assert_that([job.retry_count for job in reopened_jobs], equal_to([0, 0]))


async def test_revision_change_and_activity_invalidate_old_job_and_reuse_current_revision_job(
    docker_compose: DependencyPorts,
) -> None:
    first_source = _source("inactive/image.png", "sha256:first")
    second_source = _source("inactive/image.png", "sha256:second")

    async with _connect(docker_compose):
        first = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[first_source],
            seen_at=NOW,
            alias_enrichment_settings=SETTINGS,
        )
        changed = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[second_source],
            seen_at=NOW + timedelta(minutes=1),
            alias_enrichment_settings=SETTINGS,
        )
        after_change = await get_media_alias_enrichment_jobs()
        removed = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[],
            seen_at=NOW + timedelta(minutes=2),
            alias_enrichment_settings=SETTINGS,
        )
        after_removal = await get_media_alias_enrichment_jobs()
        restored = await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=[second_source],
            seen_at=NOW + timedelta(minutes=3),
            alias_enrichment_settings=SETTINGS,
        )
        after_restore = await get_media_alias_enrichment_jobs()

    assert_that((first.alias_enrichment_queued, changed.alias_enrichment_queued), equal_to((1, 1)))
    assert_that(
        [(job.source_revision, job.status, job.result_class) for job in after_change],
        equal_to(
            [
                ("sha256:first", MediaAliasEnrichmentJobStatus.obsolete, "stale_revision"),
                ("sha256:second", MediaAliasEnrichmentJobStatus.pending, None),
            ]
        ),
    )
    assert_that(removed.alias_enrichment_queued, equal_to(0))
    assert_that(
        after_removal[-1], has_properties(status=MediaAliasEnrichmentJobStatus.obsolete, result_class="inactive")
    )
    assert_that(restored.alias_enrichment_queued, equal_to(1))
    assert_that(len(after_restore), equal_to(2))
    assert_that(after_restore[-1].status, same_instance(MediaAliasEnrichmentJobStatus.pending))


async def test_claim_is_exclusive_until_lease_expires(docker_compose: DependencyPorts) -> None:
    sources = [_source("inactive/one.png", "sha256:one"), _source("inactive/two.png", "sha256:two")]

    async with _connect(docker_compose):
        await reconcile_category_media_snapshot(
            subscription_type_id=1,
            sources=sources,
            seen_at=NOW,
            alias_enrichment_settings=SETTINGS,
        )
        async with transaction():
            first = await claim_media_alias_enrichment_jobs(
                limit=1,
                lease_token="owner-a",
                lease_ttl=timedelta(minutes=5),
                model="gemma3:12b",
                prompt_sha256=SETTINGS.prompt_sha256 or "",
                claimed_at=NOW,
            )
        async with transaction():
            second = await claim_media_alias_enrichment_jobs(
                limit=2,
                lease_token="owner-b",
                lease_ttl=timedelta(minutes=5),
                model="gemma3:12b",
                prompt_sha256=SETTINGS.prompt_sha256 or "",
                claimed_at=NOW,
            )
        async with transaction():
            refreshed = await refresh_media_alias_enrichment_job_lease(
                job_id=second[0].id,
                lease_token="owner-b",
                leased_until=NOW + timedelta(minutes=10),
                refreshed_at=NOW + timedelta(minutes=1),
            )
            rejected_refresh = await refresh_media_alias_enrichment_job_lease(
                job_id=second[0].id,
                lease_token="another-owner",
                leased_until=NOW + timedelta(minutes=10),
                refreshed_at=NOW + timedelta(minutes=1),
            )
        async with transaction():
            before_expiry = await claim_media_alias_enrichment_jobs(
                limit=2,
                lease_token="owner-c",
                lease_ttl=timedelta(minutes=5),
                model="gemma3:12b",
                prompt_sha256=SETTINGS.prompt_sha256 or "",
                claimed_at=NOW + timedelta(minutes=4),
            )
        async with transaction():
            reclaimed = await claim_media_alias_enrichment_jobs(
                limit=1,
                lease_token="owner-c",
                lease_ttl=timedelta(minutes=5),
                model="gemma3:12b",
                prompt_sha256=SETTINGS.prompt_sha256 or "",
                claimed_at=NOW + timedelta(minutes=6),
            )

    assert_that([job.id for job in first], equal_to([1]))
    assert_that([job.id for job in second], equal_to([2]))
    assert_that((refreshed, rejected_refresh), equal_to((True, False)))
    assert_that(before_expiry, empty())
    assert_that(
        reclaimed[0],
        has_properties(
            id=1,
            status=MediaAliasEnrichmentJobStatus.processing,
            attempt_count=2,
            retry_count=2,
            lease_token="owner-c",
            leased_until=NOW + timedelta(minutes=11),
        ),
    )


def _source(path: str, revision: str) -> CategoryMediaSource:
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

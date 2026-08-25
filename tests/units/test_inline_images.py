from collections.abc import Iterable, MutableSequence
from datetime import UTC, datetime, time

from pytest import MonkeyPatch

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaStatus,
    SubscriptionType,
    TelegramMediaType,
)
from cringe_pics_telebot.services import inline_images
from cringe_pics_telebot.services.inline_metrics import InlineQueryMetrics
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia


async def test_get_inline_images_uses_catalog_ids_and_resolves_only_pending_urls(
    monkeypatch: MonkeyPatch,
) -> None:
    media = [_media(1, file_id="telegram-file-id"), _media(2)]
    requested_paths: list[str] = []

    async def get_media(category_ids: list[int]) -> list[CategoryMedia]:
        assert category_ids == [2]
        return media

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        requested_paths.extend(paths)
        return [f"https://storage.example/{path}" for path in requested_paths]

    monkeypatch.setattr(inline_images, "get_category_media_by_subscription_types", get_media)
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    subscription_type = _subscription_type()
    with InlineQueryMetrics.start(query_is_empty=False, clock=lambda: 1) as metrics:
        assert await inline_images.get_inline_images([subscription_type], shuffler=_keep_order) == [
            (
                subscription_type,
                CachedMedia(
                    name="1.png",
                    mime_type="image/png",
                    path="day/1.png",
                    source_revision="sha256:1",
                    id="telegram-file-id",
                ),
            ),
            (
                subscription_type,
                LinkedMedia(
                    name="2.png",
                    mime_type="image/png",
                    path="day/2.png",
                    source_revision="sha256:2",
                    url="https://storage.example/day/2.png",
                ),
            ),
        ]
    assert requested_paths == ["day/2.png"]
    assert metrics.counts.catalog_media == 2
    assert metrics.counts.selected_media == 2
    assert metrics.counts.ready_media == 1
    assert metrics.counts.pending_media == 1
    assert metrics.counts.url_successes == 1
    assert metrics.counts.url_failures == 0
    assert metrics.counts.postgres_calls == 1
    assert metrics.counts.yandex_calls == 1


async def test_get_inline_images_applies_global_limit_before_resolving_urls(monkeypatch: MonkeyPatch) -> None:
    media = [_media(index) for index in range(52)]
    requested_paths: list[str] = []

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        requested_paths.extend(paths)
        return [f"https://storage.example/{path}" for path in requested_paths]

    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result(media),
    )
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    results = await inline_images.get_inline_images([_subscription_type()], shuffler=_keep_order)

    assert len(results) == inline_images.MAX_INLINE_QUERY_RESULTS
    assert requested_paths == [f"day/{index}.png" for index in range(50)]


async def test_get_inline_images_skips_only_missing_download_url(monkeypatch: MonkeyPatch) -> None:
    media = [_media(1), _media(2)]
    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result(media),
    )
    monkeypatch.setattr(
        inline_images,
        "get_download_urls",
        lambda paths: _async_result(["https://storage.example/day/1.png", None]),
    )

    subscription_type = _subscription_type()
    assert await inline_images.get_inline_images([subscription_type], shuffler=_keep_order) == [
        (
            subscription_type,
            LinkedMedia(
                name="1.png",
                mime_type="image/png",
                path="day/1.png",
                source_revision="sha256:1",
                url="https://storage.example/day/1.png",
            ),
        )
    ]


async def test_get_inline_images_uses_only_ready_when_ready_fills_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    media = [*[_media(index, file_id=f"telegram-{index}") for index in range(50)], _media(50)]
    url_resolution_attempted = False

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        nonlocal url_resolution_attempted
        url_resolution_attempted = True
        return []

    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result(media),
    )
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    results = await inline_images.get_inline_images([_subscription_type()], shuffler=_keep_order)

    assert len(results) == inline_images.MAX_INLINE_QUERY_RESULTS
    assert all(isinstance(image, CachedMedia) for _, image in results)
    assert url_resolution_attempted is False


async def test_get_inline_images_fills_only_remaining_places_with_pending(
    monkeypatch: MonkeyPatch,
) -> None:
    media = [
        *[_media(index, file_id=f"telegram-{index}") for index in range(48)],
        *[_media(index) for index in range(48, 51)],
    ]
    requested_paths: list[str] = []

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        requested_paths.extend(paths)
        return [f"https://storage.example/{path}" for path in paths]

    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result(media),
    )
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    results = await inline_images.get_inline_images([_subscription_type()], shuffler=_keep_order)

    assert len(results) == inline_images.MAX_INLINE_QUERY_RESULTS
    assert sum(isinstance(image, CachedMedia) for _, image in results) == 48
    assert sum(isinstance(image, LinkedMedia) for _, image in results) == 2
    assert requested_paths == ["day/48.png", "day/49.png"]


async def test_get_inline_images_prefers_ready_duplicate_across_categories(
    monkeypatch: MonkeyPatch,
) -> None:
    morning = _subscription_type(1, name="/morning", directory="morning")
    day = _subscription_type(2, name="/day", directory="day")
    media = [
        _media(1, category_id=1, source_path="shared/image.png"),
        _media(
            2,
            category_id=2,
            source_path="shared/image.png",
            file_id="telegram-shared",
        ),
    ]
    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result(media),
    )

    results = await inline_images.get_inline_images([morning, day], shuffler=_keep_order)

    assert results == [
        (
            day,
            CachedMedia(
                name="2.png",
                mime_type="image/png",
                path="shared/image.png",
                source_revision="sha256:2",
                id="telegram-shared",
            ),
        )
    ]


def test_select_inline_media_shuffles_ready_and_pending_independently() -> None:
    media = [
        _media(1, file_id="telegram-1"),
        _media(2, file_id="telegram-2"),
        _media(3),
        _media(4),
    ]
    shuffled_inputs: list[list[int]] = []

    def reverse(items: MutableSequence[CategoryMedia]) -> None:
        shuffled_inputs.append([item.id for item in items])
        items.reverse()

    selected = inline_images._select_inline_media(
        media,
        subscription_types=[_subscription_type()],
        limit=None,
        shuffler=reverse,
    )

    assert shuffled_inputs == [[1, 2], [3, 4]]
    assert [item.id for item in selected] == [2, 1, 4, 3]


async def test_get_inline_images_returns_empty_without_url_resolution(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result([]),
    )

    assert await inline_images.get_inline_images([_subscription_type()], shuffler=_keep_order) == []


async def _async_result[T](value: T) -> T:
    return value


def _keep_order(items: MutableSequence[CategoryMedia]) -> None:
    return None


def _media(
    media_id: int,
    *,
    category_id: int = 2,
    source_path: str | None = None,
    file_id: str | None = None,
) -> CategoryMedia:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return CategoryMedia(
        id=media_id,
        subscription_type_id=category_id,
        source_path=source_path or f"day/{media_id}.png",
        source_revision=f"sha256:{media_id}",
        name=f"{media_id}.png",
        mime_type="image/png",
        telegram_media_type=TelegramMediaType.photo,
        telegram_file_id=file_id,
        telegram_file_unique_id="unique" if file_id is not None else None,
        is_active=True,
        status=CategoryMediaStatus.ready if file_id is not None else CategoryMediaStatus.pending,
        last_seen_at=now,
        materialized_at=now if file_id is not None else None,
        created_at=now,
        updated_at=now,
    )


def _subscription_type(
    subscription_type_id: int = 2,
    *,
    name: str = "/day",
    directory: str = "day",
) -> SubscriptionType:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return SubscriptionType(
        id=subscription_type_id,
        name=name,
        time=time(13),
        s3_directory_path=directory,
        search_aliases=(),
        created_at=now,
        updated_at=now,
    )

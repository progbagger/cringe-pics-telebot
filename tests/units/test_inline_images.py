from collections.abc import Iterable, MutableSequence, Sequence
from datetime import UTC, datetime, time

from hamcrest import assert_that, equal_to, has_properties, instance_of
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


async def test_get_inline_category_images_selects_one_per_nonempty_category_and_prefers_ready(
    monkeypatch: MonkeyPatch,
) -> None:
    morning = _subscription_type(1, name="/morning", directory="morning")
    day = _subscription_type(2, name="/day", directory="day")
    empty = _subscription_type(3, name="/empty", directory="empty")
    media = [
        _media(1, category_id=1, source_path="morning/1.png"),
        _media(2, category_id=1, source_path="morning/2.png"),
        _media(3, category_id=2, source_path="day/ready.png", file_id="telegram-ready"),
        _media(4, category_id=2, source_path="day/pending.png"),
    ]
    chooser_inputs: list[list[int]] = []
    requested_paths: list[str] = []

    def choose_last(items: Sequence[CategoryMedia]) -> CategoryMedia:
        chooser_inputs.append([item.id for item in items])
        return items[-1]

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        requested_paths.extend(paths)
        return [f"https://storage.example/{path}" for path in paths]

    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result(media),
    )
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    with InlineQueryMetrics.start(query_is_empty=True, clock=lambda: 1) as metrics:
        results = await inline_images.get_inline_category_images(
            [morning, day, empty],
            chooser=choose_last,
        )

    assert_that(chooser_inputs, equal_to([[1, 2], [3]]))
    assert_that(
        [(category.id, image.path) for category, image in results],
        equal_to([(1, "morning/2.png"), (2, "day/ready.png")]),
    )
    assert_that(results[0][1], instance_of(LinkedMedia))
    assert_that(results[1][1], instance_of(CachedMedia))
    assert_that(requested_paths, equal_to(["morning/2.png"]))
    assert_that(
        metrics.counts,
        has_properties(
            catalog_media=4,
            selected_media=2,
            ready_media=1,
            pending_media=1,
            url_successes=1,
            url_failures=0,
            postgres_calls=1,
            yandex_calls=1,
        ),
    )


async def test_get_inline_category_images_keeps_overlapping_paths_and_isolates_url_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    categories = [
        _subscription_type(1, name="/morning", directory="morning"),
        _subscription_type(2, name="/day", directory="day"),
        _subscription_type(3, name="/evening", directory="evening"),
    ]
    media = [
        _media(1, category_id=1, source_path="shared/image.png"),
        _media(2, category_id=2, source_path="shared/image.png"),
        _media(3, category_id=3, source_path="broken/image.png"),
    ]
    requested_paths: list[str] = []

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        requested_paths.extend(paths)
        return ["https://storage.example/shared.png", None]

    monkeypatch.setattr(
        inline_images,
        "get_category_media_by_subscription_types",
        lambda category_ids: _async_result(media),
    )
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    results = await inline_images.get_inline_category_images(categories, chooser=lambda items: items[0])

    assert_that(requested_paths, equal_to(["shared/image.png", "broken/image.png"]))
    assert_that(
        [(category.id, image.path) for category, image in results],
        equal_to([(1, "shared/image.png"), (2, "shared/image.png")]),
    )
    assert_that(results[0][1], instance_of(LinkedMedia))
    assert_that(results[1][1], instance_of(LinkedMedia))


def test_select_inline_category_media_chooses_before_stable_category_limit() -> None:
    categories = [
        _subscription_type(index, name=f"/category-{index}", directory=f"category-{index}") for index in range(1, 52)
    ]
    media = [
        _media(
            index,
            category_id=index,
            source_path=f"category-{index}/image.png",
        )
        for index in range(1, 52)
    ]
    chooser_inputs: list[list[int]] = []

    def choose_only(items: Sequence[CategoryMedia]) -> CategoryMedia:
        chooser_inputs.append([item.id for item in items])
        return items[0]

    selected = inline_images._select_inline_category_media(
        media,
        subscription_types=categories,
        limit=inline_images.MAX_INLINE_QUERY_RESULTS,
        chooser=choose_only,
    )

    assert_that(chooser_inputs, equal_to([[index] for index in range(1, 52)]))
    assert_that([item.id for item in selected], equal_to(list(range(1, 51))))


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

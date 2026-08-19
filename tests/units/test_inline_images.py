from collections.abc import Iterable
from datetime import UTC, datetime, time

from pytest import MonkeyPatch

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaStatus,
    SubscriptionType,
    TelegramMediaType,
)
from cringe_pics_telebot.services import inline_images
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
    assert await inline_images.get_inline_images([subscription_type]) == [
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


async def test_get_inline_images_limits_before_resolving_urls(monkeypatch: MonkeyPatch) -> None:
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

    results = await inline_images.get_inline_images([_subscription_type()])

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
    assert await inline_images.get_inline_images([subscription_type]) == [
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


async def _async_result[T](value: T) -> T:
    return value


def _media(media_id: int, *, file_id: str | None = None) -> CategoryMedia:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return CategoryMedia(
        id=media_id,
        subscription_type_id=2,
        source_path=f"day/{media_id}.png",
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


def _subscription_type() -> SubscriptionType:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return SubscriptionType(
        id=2,
        name="/day",
        time=time(13),
        s3_directory_path="day",
        search_aliases=(),
        created_at=now,
        updated_at=now,
    )

from collections.abc import AsyncGenerator, Iterable
from datetime import UTC, datetime, time

from pytest import MonkeyPatch

from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.repositories.yandex import Image
from cringe_pics_telebot.services import inline_images
from cringe_pics_telebot.services.inline_images import CachedInlineImage, LinkedInlineImage


async def test_get_inline_images_reuses_cached_ids_and_resolves_missing_urls(monkeypatch: MonkeyPatch) -> None:
    images = [
        Image(name="cached.png", mime_type="image/png", path="day/cached.png"),
        Image(name="linked.gif", mime_type="image/gif", path="day/linked.gif"),
    ]
    requested_paths: list[str] = []

    async def list_images(directory: str) -> AsyncGenerator[Image]:
        assert directory == "day"
        for image in images:
            yield image

    async def get_cached_file_id(*, key: str, cls: type[str]) -> str | None:
        assert cls is str
        return "telegram-file-id" if key == "day/cached.png" else None

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        requested_paths.extend(paths)
        return [f"https://storage.example/{path}" for path in requested_paths]

    monkeypatch.setattr(inline_images, "list_dir", list_images)
    monkeypatch.setattr(inline_images.cache, "get", get_cached_file_id)
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    results = await inline_images.get_inline_images(_subscription_type())

    assert results == [
        CachedInlineImage(
            name="cached.png",
            mime_type="image/png",
            path="day/cached.png",
            file_id="telegram-file-id",
        ),
        LinkedInlineImage(
            name="linked.gif",
            mime_type="image/gif",
            path="day/linked.gif",
            url="https://storage.example/day/linked.gif",
        ),
    ]
    assert requested_paths == ["day/linked.gif"]


async def test_get_inline_images_limits_results(monkeypatch: MonkeyPatch) -> None:
    async def list_images(directory: str) -> AsyncGenerator[Image]:
        assert directory == "day"
        for index in range(52):
            yield Image(name=f"{index}.png", mime_type="image/png", path=f"day/{index}.png")

    async def cache_miss(*, key: str, cls: type[str]) -> None:
        assert key.startswith("day/")
        assert cls is str

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        return [f"https://storage.example/{path}" for path in paths]

    monkeypatch.setattr(inline_images, "list_dir", list_images)
    monkeypatch.setattr(inline_images.cache, "get", cache_miss)
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    results = await inline_images.get_inline_images(_subscription_type())

    assert len(results) == inline_images.MAX_INLINE_QUERY_RESULTS
    assert results[-1].path == "day/49.png"


async def test_get_inline_images_skips_only_missing_download_url(monkeypatch: MonkeyPatch) -> None:
    images = [
        Image(name="available.png", mime_type="image/png", path="day/available.png"),
        Image(name="unavailable.png", mime_type="image/png", path="day/unavailable.png"),
    ]

    async def list_images(directory: str) -> AsyncGenerator[Image]:
        assert directory == "day"
        for image in images:
            yield image

    async def cache_miss(*, key: str, cls: type[str]) -> None:
        assert key.startswith("day/")
        assert cls is str

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        assert list(paths) == ["day/available.png", "day/unavailable.png"]
        return ["https://storage.example/day/available.png", None]

    monkeypatch.setattr(inline_images, "list_dir", list_images)
    monkeypatch.setattr(inline_images.cache, "get", cache_miss)
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    assert await inline_images.get_inline_images(_subscription_type()) == [
        LinkedInlineImage(
            name="available.png",
            mime_type="image/png",
            path="day/available.png",
            url="https://storage.example/day/available.png",
        )
    ]


def _subscription_type() -> SubscriptionType:
    now = datetime.now(UTC)
    return SubscriptionType(
        id=2,
        name="/day",
        time=time(13, 0, tzinfo=UTC),
        s3_directory_path="day",
        created_at=now,
        updated_at=now,
    )

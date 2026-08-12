import asyncio
from collections.abc import AsyncGenerator, Iterable
from datetime import UTC, datetime, time

import pytest
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


@pytest.mark.parametrize(
    ("limit", "expected_count"),
    [
        (inline_images.MAX_INLINE_QUERY_RESULTS, 50),
        (None, 52),
    ],
)
async def test_get_inline_images_supports_limited_and_complete_results(
    monkeypatch: MonkeyPatch,
    limit: int | None,
    expected_count: int,
) -> None:
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

    results = await inline_images.get_inline_images(_subscription_type(), limit=limit)

    assert len(results) == expected_count
    assert results[-1].path == f"day/{expected_count - 1}.png"


async def test_get_inline_images_reads_cache_concurrently_and_preserves_storage_order(
    monkeypatch: MonkeyPatch,
) -> None:
    images = [
        Image(name="first.png", mime_type="image/png", path="day/first.png"),
        Image(name="second.png", mime_type="image/png", path="day/second.png"),
        Image(name="third.png", mime_type="image/png", path="day/third.png"),
    ]
    cache = _ControlledCache([image.path for image in images])

    async def list_images(directory: str) -> AsyncGenerator[Image]:
        assert directory == "day"
        for image in images:
            yield image

    async def unexpected_urls(paths: Iterable[str]) -> list[str | None]:
        raise AssertionError(f"Cached images must not request URLs: {list(paths)}")

    monkeypatch.setattr(inline_images, "list_dir", list_images)
    monkeypatch.setattr(inline_images.cache, "get", cache.get)
    monkeypatch.setattr(inline_images, "get_download_urls", unexpected_urls)

    images_task = asyncio.create_task(inline_images.get_inline_images(_subscription_type()))
    await asyncio.wait_for(cache.all_started.wait(), timeout=1)
    assert not images_task.done()

    for path in reversed([image.path for image in images]):
        cache.complete(path, file_id=f"telegram-{path}")

    assert await images_task == [
        CachedInlineImage(
            name=image.name,
            mime_type=image.mime_type,
            path=image.path,
            file_id=f"telegram-{image.path}",
        )
        for image in images
    ]
    assert cache.completion_order == list(reversed([image.path for image in images]))


async def test_get_inline_images_treats_cache_error_as_miss(monkeypatch: MonkeyPatch) -> None:
    image = Image(name="fallback.png", mime_type="image/png", path="day/fallback.png")

    async def list_images(directory: str) -> AsyncGenerator[Image]:
        assert directory == "day"
        yield image

    async def broken_cache(*, key: str, cls: type[str]) -> None:
        assert key == image.path
        assert cls is str
        raise RuntimeError("Redis unavailable")

    async def get_urls(paths: Iterable[str]) -> list[str | None]:
        assert list(paths) == [image.path]
        return ["https://storage.example/day/fallback.png"]

    monkeypatch.setattr(inline_images, "list_dir", list_images)
    monkeypatch.setattr(inline_images.cache, "get", broken_cache)
    monkeypatch.setattr(inline_images, "get_download_urls", get_urls)

    assert await inline_images.get_inline_images(_subscription_type()) == [
        LinkedInlineImage(
            name=image.name,
            mime_type=image.mime_type,
            path=image.path,
            url="https://storage.example/day/fallback.png",
        )
    ]


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


class _ControlledCache:
    def __init__(self, paths: list[str]) -> None:
        self._expected_count = len(paths)
        self._releases = {path: asyncio.Event() for path in paths}
        self._file_ids: dict[str, str] = {}
        self._started_count = 0
        self.all_started = asyncio.Event()
        self.completion_order: list[str] = []

    async def get(self, *, key: str, cls: type[str]) -> str:
        assert cls is str
        self._started_count += 1
        if self._started_count == self._expected_count:
            self.all_started.set()

        await self._releases[key].wait()
        self.completion_order.append(key)
        return self._file_ids[key]

    def complete(self, path: str, *, file_id: str) -> None:
        self._file_ids[path] = file_id
        self._releases[path].set()

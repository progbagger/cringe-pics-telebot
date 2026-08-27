import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from hamcrest import assert_that, empty, equal_to, is_, starts_with
from pytest import MonkeyPatch

from cringe_pics_telebot.repositories.yandex import repo
from cringe_pics_telebot.repositories.yandex.yandex import resource_revision


def test_resource_revision_prefers_content_hash() -> None:
    assert_that(
        resource_revision(
            {
                "sha256": "ABCDEF",
                "md5": "ignored",
                "size": 12,
                "modified": "2026-08-19T00:00:00+00:00",
            }
        ),
        equal_to("sha256:abcdef"),
    )


def test_resource_revision_has_deterministic_metadata_fallback() -> None:
    first = resource_revision({"size": 12, "modified": "2026-08-19T00:00:00+00:00"})
    second = resource_revision({"modified": "2026-08-19T00:00:00+00:00", "size": 12})

    assert_that(first, equal_to(second))
    assert_that(first, starts_with("metadata-sha256:"))


async def test_get_download_urls_fetches_concurrently_and_preserves_input_order(monkeypatch: MonkeyPatch) -> None:
    paths = ["day/first.png", "day/second.png", "day/third.png"]
    client = _ControlledYandexClient(paths)

    @asynccontextmanager
    async def get_connection() -> AsyncGenerator[_ControlledYandexClient]:
        yield client

    monkeypatch.setattr(repo, "get_connection", get_connection)

    urls_task = asyncio.create_task(repo.get_download_urls(paths))
    await asyncio.wait_for(client.all_started.wait(), timeout=1)
    assert_that(urls_task.done(), is_(False))

    for path in reversed(paths):
        client.complete(path)

    assert_that(await urls_task, equal_to([f"https://storage.example/{path}" for path in paths]))
    assert_that(client.completion_order, equal_to(list(reversed(paths))))


async def test_get_download_urls_returns_none_for_failed_lookup_after_batch_finishes(monkeypatch: MonkeyPatch) -> None:
    paths = ["day/good.png", "day/broken.png"]
    client = _ControlledYandexClient(paths, broken_path="day/broken.png")

    @asynccontextmanager
    async def get_connection() -> AsyncGenerator[_ControlledYandexClient]:
        yield client

    monkeypatch.setattr(repo, "get_connection", get_connection)

    urls_task = asyncio.create_task(repo.get_download_urls(paths))
    await asyncio.wait_for(client.all_started.wait(), timeout=1)
    client.complete("day/broken.png")
    await asyncio.wait_for(client.completed("day/broken.png"), timeout=1)
    assert_that(urls_task.done(), is_(False))

    client.complete("day/good.png")
    assert_that(await urls_task, equal_to(["https://storage.example/day/good.png", None]))


async def test_get_download_urls_skips_connection_for_empty_input(monkeypatch: MonkeyPatch) -> None:
    @asynccontextmanager
    async def unexpected_connection() -> AsyncGenerator[None]:
        raise AssertionError("Empty input must not open a Yandex connection")
        yield

    monkeypatch.setattr(repo, "get_connection", unexpected_connection)

    assert_that(await repo.get_download_urls([]), empty())


class _ControlledYandexClient:
    def __init__(self, paths: list[str], *, broken_path: str | None = None) -> None:
        self._expected_count = len(paths)
        self._releases = {path: asyncio.Event() for path in paths}
        self._completions = {path: asyncio.Event() for path in paths}
        self._broken_path = broken_path
        self._started_count = 0
        self.all_started = asyncio.Event()
        self.completion_order: list[str] = []

    async def get_download_url(self, path: str) -> str:
        self._started_count += 1
        if self._started_count == self._expected_count:
            self.all_started.set()

        await self._releases[path].wait()
        self.completion_order.append(path)
        self._completions[path].set()
        if path == self._broken_path:
            raise RuntimeError(f"Failed to get URL for {path}")

        return f"https://storage.example/{path}"

    def complete(self, path: str) -> None:
        self._releases[path].set()

    async def completed(self, path: str) -> None:
        await self._completions[path].wait()

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from aiohttp import ClientPayloadError
from hamcrest import assert_that, contains_exactly, empty, equal_to, has_length, starts_with
from pytest import MonkeyPatch

from cringe_pics_telebot.repositories.yandex import repo, yandex
from cringe_pics_telebot.repositories.yandex.yandex import (
    YandexDownloadTooLargeError,
    YandexS3Client,
    resource_revision,
)


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
    assert urls_task.done() is False

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
    assert urls_task.done() is False

    client.complete("day/good.png")
    assert_that(await urls_task, equal_to(["https://storage.example/day/good.png", None]))


async def test_get_download_urls_skips_connection_for_empty_input(monkeypatch: MonkeyPatch) -> None:
    @asynccontextmanager
    async def unexpected_connection() -> AsyncGenerator[None]:
        raise AssertionError("Empty input must not open a Yandex connection")
        yield

    monkeypatch.setattr(repo, "get_connection", unexpected_connection)

    assert_that(await repo.get_download_urls([]), empty())


async def test_download_file_uses_fresh_url_without_oauth_and_streams_content(monkeypatch: MonkeyPatch) -> None:
    api_response = _FakeResponse(json_value={"href": "https://download.example/media"})
    download_response = _FakeResponse(
        chunks=(b"first", b"second"),
        content_length=11,
        headers={"Content-Type": "image/png"},
    )
    sessions = _install_fake_sessions(monkeypatch, (api_response,), (download_response,))

    async with YandexS3Client("secret", api_base_url="https://api.example/") as client:
        result = await client.download_file(
            "day/image.png",
            max_bytes=11,
            timeout=timedelta(seconds=1),
        )

    assert result.content == b"firstsecond"
    assert result.content_type == "image/png"
    assert_that(sessions[0].headers, equal_to({"Authorization": "OAuth secret"}))
    assert_that(sessions[1].headers, empty())
    assert_that(sessions[1].requests, contains_exactly("https://download.example/media"))
    for session in sessions:
        assert session.exited is True
    assert api_response.exited is True
    assert download_response.exited is True


async def test_each_download_looks_up_a_new_url(monkeypatch: MonkeyPatch) -> None:
    sessions = _install_fake_sessions(
        monkeypatch,
        (
            _FakeResponse(json_value={"href": "https://download.example/first"}),
            _FakeResponse(json_value={"href": "https://download.example/second"}),
        ),
        (_FakeResponse(chunks=(b"first",)), _FakeResponse(chunks=(b"second",))),
    )
    async with YandexS3Client("secret") as client:
        first = await client.download_file("day/image.png", max_bytes=10, timeout=timedelta(seconds=1))
        second = await client.download_file("day/image.png", max_bytes=10, timeout=timedelta(seconds=1))

    assert first.content == b"first"
    assert second.content == b"second"
    assert_that(sessions[0].requests, has_length(2))
    assert_that(
        sessions[1].requests, contains_exactly("https://download.example/first", "https://download.example/second")
    )


@pytest.mark.parametrize(
    ("chunks", "content_length"),
    [
        ((), 12),
        ((b"123456", b"789012"), None),
    ],
)
async def test_download_file_rejects_declared_or_streamed_oversize(
    monkeypatch: MonkeyPatch,
    chunks: tuple[bytes, ...],
    content_length: int | None,
) -> None:
    download_response = _FakeResponse(chunks=chunks, content_length=content_length)
    _install_fake_sessions(
        monkeypatch,
        (_FakeResponse(json_value={"href": "https://download.example/media"}),),
        (download_response,),
    )

    async with YandexS3Client("secret") as client:
        with pytest.raises(YandexDownloadTooLargeError, match="byte limit"):
            await client.download_file("day/image.png", max_bytes=10, timeout=timedelta(seconds=1))

    assert download_response.exited is True


async def test_download_file_timeout_closes_active_response(monkeypatch: MonkeyPatch) -> None:
    download_response = _BlockingResponse()
    _install_fake_sessions(
        monkeypatch,
        (_FakeResponse(json_value={"href": "https://download.example/media"}),),
        (download_response,),
    )

    async with YandexS3Client("secret") as client:
        with pytest.raises(TimeoutError):
            await client.download_file("day/image.png", max_bytes=10, timeout=timedelta(milliseconds=1))

    assert download_response.exited is True


async def test_download_file_cancellation_closes_active_response(monkeypatch: MonkeyPatch) -> None:
    download_response = _BlockingResponse()
    _install_fake_sessions(
        monkeypatch,
        (_FakeResponse(json_value={"href": "https://download.example/media"}),),
        (download_response,),
    )

    async with YandexS3Client("secret") as client:
        task = asyncio.create_task(client.download_file("day/image.png", max_bytes=10, timeout=timedelta(seconds=10)))
        await asyncio.wait_for(download_response.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert download_response.exited is True


async def test_download_transport_error_closes_active_response(monkeypatch: MonkeyPatch) -> None:
    download_response = _FakeResponse()
    download_response.content = _FailingContent(())
    _install_fake_sessions(
        monkeypatch,
        (_FakeResponse(json_value={"href": "https://download.example/media"}),),
        (download_response,),
    )
    async with YandexS3Client("secret") as client:
        with pytest.raises(ClientPayloadError):
            await client.download_file("day/image.png", max_bytes=10, timeout=timedelta(seconds=1))

    assert download_response.exited is True


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


class _FakeContent:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int) -> AsyncGenerator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        *,
        json_value: dict[str, str] | None = None,
        chunks: tuple[bytes, ...] = (),
        content_length: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._json_value = json_value
        self.content = _FakeContent(chunks)
        self.content_length = content_length
        self.headers = headers or {}
        self.exited = False

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True

    async def json(self) -> dict[str, str]:
        if self._json_value is None:
            raise AssertionError("Response has no JSON value")
        return self._json_value


class _BlockingContent(_FakeContent):
    def __init__(self, started: asyncio.Event) -> None:
        super().__init__(())
        self._started = started

    async def iter_chunked(self, size: int) -> AsyncGenerator[bytes]:
        self._started.set()
        await asyncio.Event().wait()
        yield b"unreachable"


class _FailingContent(_FakeContent):
    async def iter_chunked(self, size: int) -> AsyncGenerator[bytes]:
        raise ClientPayloadError("Incomplete body")
        yield b"unreachable"


class _BlockingResponse(_FakeResponse):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.content = _BlockingContent(self.started)


class _FakeSession:
    def __init__(self, responses: tuple[_FakeResponse, ...], kwargs: dict[str, Any]) -> None:
        self._responses = iter(responses)
        self.headers = dict(kwargs.get("headers", {}))
        self.requests: list[str] = []
        self.exited = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append(url)
        return next(self._responses)


def _install_fake_sessions(
    monkeypatch: MonkeyPatch,
    api_responses: tuple[_FakeResponse, ...],
    download_responses: tuple[_FakeResponse, ...],
) -> list[_FakeSession]:
    sessions: list[_FakeSession] = []
    response_groups = iter((api_responses, download_responses))

    def create_session(**kwargs: Any) -> _FakeSession:
        session = _FakeSession(next(response_groups), kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(yandex.aiohttp, "ClientSession", create_session)
    return sessions

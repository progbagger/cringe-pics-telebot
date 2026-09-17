import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from hamcrest import assert_that, contains_exactly, contains_string, equal_to, has_entries, has_length, not_

from cringe_pics_telebot.repositories.ollama import (
    InvalidOllamaResponseError,
    OllamaClient,
    OllamaHTTPError,
    OllamaTimeoutError,
    OllamaTransportError,
)
from cringe_pics_telebot.repositories.ollama.client import MAX_OLLAMA_ERROR_LENGTH, MAX_OLLAMA_RESPONSE_BYTES


def _response(aliases: Any) -> bytes:
    return json.dumps({"message": {"content": json.dumps({"aliases": aliases})}}).encode()


@dataclass(slots=True)
class FakeOllama:
    base_url: str = ""
    status: int = 200
    body: bytes = field(default_factory=lambda: _response(["Сонный кот", "Кофе"]))
    blocked: bool = False
    disconnect: bool = False
    chunked: bool = False
    started: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def chat(self, request: web.Request) -> web.StreamResponse:
        self.requests.append({"path": request.path, "headers": dict(request.headers), "payload": await request.json()})
        self.started.set()

        if self.blocked:
            await self.released.wait()
        if self.disconnect and request.transport is not None:
            request.transport.close()

        if self.chunked:
            response = web.StreamResponse(status=self.status, headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(self.body)
            await response.write_eof()
            return response

        return web.Response(body=self.body, status=self.status, content_type="application/json")


@pytest.fixture
async def fake_ollama() -> AsyncIterator[FakeOllama]:
    fake = FakeOllama()
    app = web.Application()
    app.router.add_post("/api/chat", fake.chat)
    async with TestServer(app) as server:
        fake.base_url = str(server.make_url("/")).rstrip("/")
        try:
            yield fake
        finally:
            fake.released.set()


@pytest.mark.parametrize("api_key", [None, "", "secret-token"])
async def test_native_chat_contract_and_optional_bearer(*, fake_ollama: FakeOllama, api_key: str | None) -> None:
    async with OllamaClient(
        base_url=f"{fake_ollama.base_url}/",
        request_timeout=timedelta(seconds=1),
        api_key=api_key,
    ) as client:
        result = await client.generate_aliases(image=b"jpeg-image", model="vision-model", prompt="Опиши изображение")

    assert_that(result, equal_to(("Сонный кот", "Кофе")))
    assert_that(fake_ollama.requests, has_length(1))
    request = fake_ollama.requests[0]
    assert request["path"] == "/api/chat"
    assert request["headers"].get("Authorization") == (f"Bearer {api_key}" if api_key else None)
    assert_that(
        request["payload"],
        has_entries(
            model="vision-model",
            messages=[
                {
                    "role": "user",
                    "content": "Опиши изображение",
                    "images": [base64.b64encode(b"jpeg-image").decode()],
                }
            ],
            stream=False,
            format={
                "type": "object",
                "properties": {
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 100},
                        "minItems": 1,
                        "maxItems": 20,
                    }
                },
                "required": ["aliases"],
                "additionalProperties": False,
            },
            options={"temperature": 0},
        ),
    )


@pytest.mark.parametrize("status", [302, 400, 401, 429, 500, 503])
async def test_http_error_retains_status_and_redacts_request_content(*, fake_ollama: FakeOllama, status: int) -> None:
    fake_ollama.status = status
    prompt = "private-prompt"
    image = b"private-image"
    fake_ollama.body = json.dumps(
        {"error": f"secret-token\n{prompt} {base64.b64encode(image).decode()}\x00\x1b " + "x" * 500}
    ).encode()

    async with OllamaClient(
        base_url=fake_ollama.base_url,
        request_timeout=timedelta(seconds=1),
        api_key="secret-token",
    ) as client:
        with pytest.raises(OllamaHTTPError) as raised:
            await client.generate_aliases(image=image, model="vision-model", prompt=prompt)

    assert raised.value.status == status
    assert len(raised.value.error_message) <= MAX_OLLAMA_ERROR_LENGTH
    for private_value in (prompt, "secret-token", base64.b64encode(image).decode()):
        assert_that(str(raised.value), not_(contains_string(private_value)))
    assert_that(raised.value.error_message, contains_string("[redacted]"))
    assert all(character.isprintable() for character in raised.value.error_message)


@pytest.mark.parametrize("body", [b"not-json", b'{"error": 42}', b"x" * (MAX_OLLAMA_RESPONSE_BYTES + 1)])
async def test_unstructured_or_oversized_http_error_does_not_expose_body(
    *, fake_ollama: FakeOllama, body: bytes
) -> None:
    fake_ollama.status = 500
    fake_ollama.body = body
    async with OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(seconds=1)) as client:
        with pytest.raises(OllamaHTTPError) as raised:
            await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt")

    assert raised.value.error_message == "No safe structured error was provided"


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"\xff",
        b"[" * 2_000 + b"]" * 2_000,
        b"[]",
        b"{}",
        b'{"message": {"content": 42}}',
        b'{"message": {"content": "not-json"}}',
        b'{"message": {"content": "[]"}}',
        json.dumps({"message": {"content": json.dumps({"aliases": ["cat"], "extra": True})}}).encode(),
        _response(None),
        _response([]),
        _response(["cat"] * 21),
        _response([42]),
        _response([""]),
        _response(["x" * 101]),
    ],
)
async def test_malformed_or_invalid_structured_output_is_rejected(*, fake_ollama: FakeOllama, body: bytes) -> None:
    fake_ollama.body = body
    async with OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(seconds=1)) as client:
        with pytest.raises(InvalidOllamaResponseError):
            await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt")


@pytest.mark.parametrize("chunked", [False, True])
async def test_declared_and_streamed_response_size_is_bounded(*, fake_ollama: FakeOllama, chunked: bool) -> None:
    fake_ollama.chunked = chunked
    fake_ollama.body = b"x" * (MAX_OLLAMA_RESPONSE_BYTES + 1)
    async with OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(seconds=1)) as client:
        with pytest.raises(InvalidOllamaResponseError, match="byte limit"):
            await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt")


async def test_schema_boundaries_are_accepted_without_repository_normalization(fake_ollama: FakeOllama) -> None:
    aliases = [" /Кот "] + ["x" * 100] * 19
    fake_ollama.body = _response(aliases)
    async with OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(seconds=1)) as client:
        result = await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt")

    assert_that(result, contains_exactly(*aliases))


async def test_timeout_is_typed_and_session_remains_usable(fake_ollama: FakeOllama) -> None:
    fake_ollama.blocked = True
    async with OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(milliseconds=20)) as client:
        with pytest.raises(OllamaTimeoutError):
            await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt")

        fake_ollama.released.set()
        fake_ollama.blocked = False
        assert_that(
            await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt"),
            equal_to(("Сонный кот", "Кофе")),
        )


async def test_transport_failure_is_typed(fake_ollama: FakeOllama) -> None:
    fake_ollama.disconnect = True
    async with OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(seconds=1)) as client:
        with pytest.raises(OllamaTransportError):
            await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt")


async def test_cancellation_propagates_from_inflight_request(fake_ollama: FakeOllama) -> None:
    fake_ollama.blocked = True
    async with OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(seconds=1)) as client:
        task = asyncio.create_task(client.generate_aliases(image=b"image", model="vision-model", prompt="prompt"))
        await asyncio.wait_for(fake_ollama.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        fake_ollama.released.set()


async def test_closed_client_rejects_calls_without_creating_session(fake_ollama: FakeOllama) -> None:
    client = OllamaClient(base_url=fake_ollama.base_url, request_timeout=timedelta(seconds=1))
    async with client:
        pass

    with pytest.raises(RuntimeError, match="not connected"):
        await client.generate_aliases(image=b"image", model="vision-model", prompt="prompt")

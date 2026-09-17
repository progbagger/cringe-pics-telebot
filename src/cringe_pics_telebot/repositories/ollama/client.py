import asyncio
import base64
import json
from datetime import timedelta
from types import TracebackType
from typing import Any

import aiohttp

from cringe_pics_telebot.entities.generated_aliases import MAX_GENERATED_ALIAS_LENGTH, MAX_GENERATED_ALIASES

MAX_OLLAMA_RESPONSE_BYTES = 64 * 1024
MAX_OLLAMA_ERROR_LENGTH = 256


class OllamaError(RuntimeError): ...


class OllamaTransportError(OllamaError): ...


class OllamaTimeoutError(OllamaTransportError): ...


class InvalidOllamaResponseError(OllamaError): ...


class OllamaHTTPError(OllamaError):
    def __init__(self, *, status: int, error_message: str) -> None:
        self.status = status
        self.error_message = error_message
        super().__init__(f"Ollama HTTP {status}: {error_message}")


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str,
        request_timeout: timedelta,
        api_key: str | None = None,
    ) -> None:
        if request_timeout.total_seconds() <= 0:
            raise ValueError("Ollama request timeout must be positive")

        self._chat_url = f"{base_url.rstrip('/')}/api/chat"
        self._timeout = aiohttp.ClientTimeout(total=request_timeout.total_seconds())
        self._api_key = api_key.strip() if api_key else None
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> OllamaClient:
        if self._session is not None:
            raise RuntimeError("Ollama client is already connected")

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        self._session = aiohttp.ClientSession(headers=headers, timeout=self._timeout)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def generate_aliases(self, *, image: bytes, model: str, prompt: str) -> tuple[str, ...]:
        if self._session is None:
            raise RuntimeError("Ollama client is not connected")

        encoded_image = base64.b64encode(image).decode("ascii")
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": [encoded_image]}],
            "stream": False,
            "format": _aliases_json_schema(),
            "options": {"temperature": 0},
        }

        try:
            async with self._session.post(self._chat_url, json=payload, allow_redirects=False) as response:
                if response.status >= 300:
                    error_message = await _read_http_error(
                        response=response,
                        redacted_values=(prompt, encoded_image, self._api_key or ""),
                    )
                    raise OllamaHTTPError(status=response.status, error_message=error_message)
                raw = await _read_bounded_response(response)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise OllamaTimeoutError("Ollama request timed out") from error
        except aiohttp.ClientError as error:
            raise OllamaTransportError("Ollama request failed at the HTTP transport boundary") from error

        return _parse_aliases_response(raw)


def _aliases_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "aliases": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": MAX_GENERATED_ALIAS_LENGTH},
                "minItems": 1,
                "maxItems": MAX_GENERATED_ALIASES,
            }
        },
        "required": ["aliases"],
        "additionalProperties": False,
    }


async def _read_bounded_response(response: aiohttp.ClientResponse) -> bytes:
    if response.content_length is not None and response.content_length > MAX_OLLAMA_RESPONSE_BYTES:
        raise InvalidOllamaResponseError("Ollama response exceeds byte limit")

    content = bytearray()
    async for chunk in response.content.iter_chunked(8 * 1024):
        if len(content) + len(chunk) > MAX_OLLAMA_RESPONSE_BYTES:
            raise InvalidOllamaResponseError("Ollama response exceeds byte limit")
        content.extend(chunk)

    return bytes(content)


async def _read_http_error(*, response: aiohttp.ClientResponse, redacted_values: tuple[str, ...]) -> str:
    try:
        raw = await _read_bounded_response(response)
        payload = json.loads(raw)
    except InvalidOllamaResponseError, ValueError, UnicodeDecodeError, RecursionError:
        return "No safe structured error was provided"

    if not isinstance(payload, dict) or not isinstance(payload.get("error"), str):
        return "No safe structured error was provided"

    message = payload["error"]
    for value in sorted(filter(None, redacted_values), key=len, reverse=True):
        message = message.replace(value, "[redacted]")

    message = " ".join("".join(character if character.isprintable() else " " for character in message).split())
    return message[:MAX_OLLAMA_ERROR_LENGTH] or "Empty error"


def _parse_aliases_response(raw: bytes) -> tuple[str, ...]:
    try:
        outer = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise InvalidOllamaResponseError("Ollama response is not a JSON document") from error

    if not isinstance(outer, dict) or not isinstance(outer.get("message"), dict):
        raise InvalidOllamaResponseError("Ollama response has no message object")
    content = outer["message"].get("content")
    if not isinstance(content, str):
        raise InvalidOllamaResponseError("Ollama response has no message content string")

    try:
        structured = json.loads(content)
    except (ValueError, RecursionError) as error:
        raise InvalidOllamaResponseError("Ollama message content is not a JSON document") from error

    if not isinstance(structured, dict) or set(structured) != {"aliases"}:
        raise InvalidOllamaResponseError("Ollama message content does not match the aliases object schema")
    aliases = structured["aliases"]
    if not isinstance(aliases, list) or not 1 <= len(aliases) <= MAX_GENERATED_ALIASES:
        raise InvalidOllamaResponseError("Ollama aliases array does not match count limits")

    if any(not isinstance(alias, str) or not 1 <= len(alias) <= MAX_GENERATED_ALIAS_LENGTH for alias in aliases):
        raise InvalidOllamaResponseError("Ollama aliases do not match string type or length limits")

    return tuple(aliases)

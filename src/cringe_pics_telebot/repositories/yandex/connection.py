from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from .yandex import YandexS3Client

_client: ContextVar[YandexS3Client] = ContextVar("yandex_s3_client")
_TOKEN: str | None = None


class S3ConnectionError(ConnectionError): ...


class NotConnectedError(S3ConnectionError): ...


def connect(token: str) -> None:
    global _TOKEN
    _TOKEN = token


@asynccontextmanager
async def get_connection() -> AsyncGenerator[YandexS3Client]:
    if _TOKEN is None:
        raise NotConnectedError

    try:
        yield _client.get()
    except LookupError:
        try:
            client = YandexS3Client(_TOKEN)
            token = _client.set(client)
            async with client:
                yield client
        finally:
            _client.reset(token)

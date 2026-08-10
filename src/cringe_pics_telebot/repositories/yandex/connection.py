from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from .yandex import YandexS3Client

_TOKEN: str | None = None
_API_BASE_URL: str | None = None


class S3ConnectionError(ConnectionError): ...


class NotConnectedError(S3ConnectionError): ...


def connect(token: str, *, api_base_url: str | None = None) -> None:
    global _API_BASE_URL, _TOKEN
    _TOKEN = token
    _API_BASE_URL = api_base_url


@asynccontextmanager
async def get_connection() -> AsyncGenerator[YandexS3Client]:
    if _TOKEN is None:
        raise NotConnectedError

    async with YandexS3Client(_TOKEN, api_base_url=_API_BASE_URL) as client:
        yield client

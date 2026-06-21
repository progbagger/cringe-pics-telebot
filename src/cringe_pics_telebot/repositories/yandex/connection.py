from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from .yandex import YandexS3Client

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

    async with YandexS3Client(_TOKEN) as client:
        yield client

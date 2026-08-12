import asyncio
import logging
from collections.abc import AsyncGenerator, Iterable
from typing import cast

from cringe_pics_telebot.repositories.yandex.connection import get_connection
from cringe_pics_telebot.repositories.yandex.yandex import Image

logger = logging.getLogger(__name__)


async def list_dir(dir: str) -> AsyncGenerator[Image]:
    async with get_connection() as conn:
        async for image in conn.list_dir(dir):
            yield image


async def download_file(path: str, *, dir: str | None = None) -> bytes:
    async with get_connection() as conn:
        return await conn.download_file(path=path, dir=dir)


async def get_download_urls(paths: Iterable[str]) -> list[str | None]:
    paths = list(paths)
    if not paths:
        return []

    async with get_connection() as conn:
        results = await asyncio.gather(
            *(conn.get_download_url(path) for path in paths),
            return_exceptions=True,
        )

    for path, result in zip(paths, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            logger.error("Failed to get download URL for %s", path, exc_info=result)

    return [None if isinstance(result, BaseException) else cast(str, result) for result in results]

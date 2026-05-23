from collections.abc import AsyncGenerator

from cringe_pics_telebot.repositories.yandex.connection import get_connection
from cringe_pics_telebot.repositories.yandex.yandex import Image


async def list_dir(dir: str) -> AsyncGenerator[Image]:
    async with get_connection() as conn:
        async for image in conn.list_dir(dir):
            yield image


async def download_file(path: str, *, dir: str | None = None) -> bytes:
    async with get_connection() as conn:
        return await conn.download_file(path=path, dir=dir)

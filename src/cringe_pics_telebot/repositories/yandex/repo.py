from typing import AsyncGenerator

from cringe_pics_telebot.repositories.yandex import Image
from cringe_pics_telebot.repositories.yandex import get_connection as get


async def list_dir(dir: str) -> AsyncGenerator[Image]:
    async with get() as conn:
        async for image in conn.list_dir(dir):
            yield image


async def download_file(path: str, *, dir: str | None = None) -> bytes:
    async with get() as conn:
        return await conn.download_file(path=path, dir=dir)

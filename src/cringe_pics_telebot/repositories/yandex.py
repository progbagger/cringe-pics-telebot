import asyncio
import logging
import os
from dataclasses import dataclass
from itertools import count
from typing import AsyncGenerator

import aiohttp

from cringe_pics_telebot.helpers.aiohttp_logger_middleware import (
    aiohttp_logging_middleware_factory,
)

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)


@dataclass(slots=True)
class Image:
    name: str
    """Название изображения"""
    mime_type: str
    """Мим-тип изображения"""


class YandexS3Client:
    _session: aiohttp.ClientSession

    YANDEX_DISK_API_BASE_URL = "https://cloud-api.yandex.net/v1/disk/"
    YANDEX_DISK_DOWNLOAD_BASE_URL = "https://downloader.dst.yandex.ru/disk/"

    def __init__(self, token: str) -> None:
        self._token = token

    async def __aenter__(self) -> "YandexS3Client":
        self._session = aiohttp.ClientSession(
            raise_for_status=True,
            headers={"Authorization": f"OAuth {self._token}"},
            middlewares=[aiohttp_logging_middleware_factory(_logger)],
        )
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *args, **kwargs) -> None:
        await self._session.__aexit__(*args, **kwargs)

    @classmethod
    def _create_url(cls, path: str, *, base_url: str | None = None) -> str:
        return f"{(base_url or cls.YANDEX_DISK_API_BASE_URL).rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _get_path_with_app(path: str) -> str:
        return f"app:/{path.lstrip('/')}"

    async def list_dir(self, path: str = "/") -> AsyncGenerator[Image]:
        fetch_size = 1_000
        for i in count(0, fetch_size):
            async with self._session.get(
                self._create_url("/resources"),
                params={
                    "path": self._get_path_with_app(path or ""),
                    "limit": fetch_size,
                    "offset": i,
                },
            ) as response:
                items_count = 0
                j = await response.json()
                for item in j["_embedded"]["items"]:
                    image_path: str = item["path"]

                    try:
                        mime_type: str | None = item["mime_type"]
                    except KeyError:
                        mime_type = None

                    if mime_type is not None and mime_type.startswith("image"):
                        yield Image(
                            name=image_path.split("/", 4)[-1],
                            mime_type=mime_type,
                        )

                    items_count += 1

            if items_count < fetch_size:
                break

    async def download_file(self, path: str) -> bytes:
        async with self._session.get(
            self._create_url("/resources/download"),
            params={"path": self._get_path_with_app(path), "fields": "href"},
        ) as response:
            link: str = (await response.json())["href"]

        async with self._session.get(link) as response:
            return await response.read()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    async with YandexS3Client(os.environ["YANDEX_DISK_APP_CLIENT_TOKEN"]) as s3_client:

        async def download_and_save_image(*, image_path: str, save_path: str) -> None:
            image_bytes = await s3_client.download_file(image_path)
            with open(save_path, "wb") as f:
                f.write(image_bytes)

        images = [image async for image in s3_client.list_dir("day")]

        os.makedirs("downloads", exist_ok=True)
        async with asyncio.TaskGroup() as tg:
            for image in images:
                tg.create_task(
                    download_and_save_image(
                        image_path=f"day/{image.name}",
                        save_path=os.path.join("downloads", image.name),
                    )
                )


if __name__ == "__main__":
    asyncio.run(main())

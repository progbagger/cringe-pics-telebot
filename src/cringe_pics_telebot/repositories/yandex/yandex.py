import hashlib
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from typing import Any

import aiohttp

from cringe_pics_telebot.helpers.aiohttp_logger_middleware import (
    aiohttp_logging_middleware_factory,
)

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)


@dataclass(slots=True, kw_only=True)
class Image:
    name: str
    """Название изображения"""
    mime_type: str
    """Мим-тип изображения"""
    path: str
    """Путь к изображению в S3"""
    source_revision: str
    """Стабильный идентификатор версии содержимого"""
    size: int | None = None
    """Размер файла в байтах"""
    modified_at: datetime | None = None
    """Время последнего изменения файла"""


class YandexS3Client:
    """Клиент для загрузки файлов с Яндекс.Диска"""

    _session: aiohttp.ClientSession

    YANDEX_DISK_API_BASE_URL = "https://cloud-api.yandex.net/v1/disk/"
    YANDEX_DISK_DOWNLOAD_BASE_URL = "https://downloader.dst.yandex.ru/disk/"

    def __init__(self, token: str, *, api_base_url: str | None = None, fetch_size: int = 1_000) -> None:
        """Создаёт клиент с заданным токеном приложения.

        Args:
            token (str): Токен приложения
        """

        self._token = token
        self._api_base_url = api_base_url or self.YANDEX_DISK_API_BASE_URL

        self.fetch_size = fetch_size

    async def __aenter__(self) -> YandexS3Client:
        self._session = aiohttp.ClientSession(
            raise_for_status=True,
            headers={"Authorization": f"OAuth {self._token}"},
            middlewares=[aiohttp_logging_middleware_factory(_logger)],
        )
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        await self._session.__aexit__(*args, **kwargs)

    @classmethod
    def _create_url(cls, path: str, *, base_url: str | None = None) -> str:
        """Создаёт URL для обращения в API Яндекс.Диска.

        Args:
            path (str): Конечный путь, который нужно присоединить к базовому пути API Яндекс.Диска
            base_url (str | None, optional): Если базовый путь другой (например, при скачивании объекта).

        Returns:
            str: Полный путь для обращения в API Яндекс.Диска
        """

        return f"{(base_url or cls.YANDEX_DISK_API_BASE_URL).rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _get_path_with_app(path: str) -> str:
        """Делает путь к объекту пригодным для папки приложения.

        Args:
            path (str): Путь к объекту на диске

        Returns:
            str: Путь с префиксом `app:/`
        """

        return f"app:/{path.lstrip('/')}"

    async def list_dir(self, path: str = "/") -> AsyncGenerator[Image]:
        """Перечисляет изображения в папке по указанному пути.

        Args:
            path (str, optional): Путь до папки. Defaults to "/".

        Returns:
            AsyncGenerator[Image]: Асинхронный генератор изображений в папке
        """

        for i in count(0, self.fetch_size):
            async with self._session.get(
                self._create_url("/resources", base_url=self._api_base_url),
                params={
                    "path": self._get_path_with_app(path or ""),
                    "limit": self.fetch_size,
                    "offset": i,
                },
            ) as response:
                items_count = 0
                j = await response.json()
                for item in j["_embedded"]["items"]:
                    image_path: str = item["path"]

                    # возвращаются пути вида
                    # disk:/Приложения/Название приложения/путь
                    # нас же интересует только путь, поэтому убираем префикс
                    image_path = image_path.split("/", 3)[-1]

                    try:
                        mime_type: str | None = item["mime_type"]
                    except KeyError:
                        mime_type = None

                    if mime_type is not None and mime_type.startswith("image"):
                        modified_at = datetime.fromisoformat(item["modified"])
                        yield Image(
                            name=image_path.split("/", 4)[-1],
                            mime_type=mime_type,
                            path=image_path,
                            source_revision=resource_revision(item),
                            size=item["size"],
                            modified_at=modified_at,
                        )

                    items_count += 1

            if items_count < self.fetch_size:
                break

    async def get_download_url(self, path: str, dir: str | None = None) -> str:
        """Получает временную ссылку для скачивания файла.

        Args:
            path (str): Путь к файлу, ссылку на который нужно получить
            dir (str | None, optional): Если путь неполный, можно присоединить к нему папку, указав этот параметр

        Returns:
            str: Временная ссылка для скачивания файла
        """

        if dir is not None:
            path = f"{dir.lstrip('/')}/{path.lstrip('/')}"

        async with self._session.get(
            self._create_url("/resources/download", base_url=self._api_base_url),
            params={"path": self._get_path_with_app(path), "fields": "href"},
        ) as response:
            return (await response.json())["href"]


def resource_revision(resource: dict[str, Any]) -> str:
    for algorithm in ("sha256", "md5"):
        if digest := resource.get(algorithm):
            return f"{algorithm}:{str(digest).casefold()}"

    metadata = {
        "modified": resource.get("modified"),
        "size": resource.get("size"),
    }
    if None in metadata.values():
        raise ValueError("Yandex resource has no hash or complete size/modified metadata")

    payload = json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"metadata-sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

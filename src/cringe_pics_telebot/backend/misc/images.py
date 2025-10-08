import random
from cringe_pics_telebot.db.db import DatabaseManager
from cringe_pics_telebot.repositories.yandex_disk import YandexDiskApiClient
from cringe_pics_telebot.repositories.yandex_disk import Image


class ImageManager:
    def __init__(
        self, *, db_manager: DatabaseManager, ya_disk_api_client: YandexDiskApiClient
    ) -> None:
        self._database_manager = db_manager
        self._ya_disk_api_client = ya_disk_api_client

    async def get_random_image_for_category(self, category_path: str) -> Image:
        paths = await self._ya_disk_api_client.get_images_paths(category_path)
        return await self._ya_disk_api_client.download_image(random.choice(paths))

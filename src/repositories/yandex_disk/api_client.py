import asyncio

import aiohttp
from repositories.api_client import ApiClient


class YandexDiskApiClient:
    BASE_URL = "https://cloud-api.yandex.net"

    def __init__(self, oauth_token: str) -> None:
        self._api_client = ApiClient(base_url=self.BASE_URL, oauth_token=oauth_token)

    async def get_images_paths(self, dirpath: str) -> list[tuple[str, str]]:
        params = {
            "fields": ".".join(["_embedded"]),
            "path": f"app:/{dirpath.strip("/")}",
            "limit": 1_000_000,
        }

        response = await self._api_client.get(url=f"/v1/disk/resources", params=params)
        items: list[str] = []
        for item in response["_embedded"]["items"]:
            if item["type"] == "file" and item["mime_type"].startswith("image"):
                items.append((item["path"], item["mime_type"]))

        return items

    async def download_image(self, image_path: str) -> bytes:
        params = {
            "path": image_path,
            "fields": ".".join(["href"]),
        }

        response = await self._api_client.get(
            url=f"/v1/disk/resources/download", params=params
        )

        save = self._api_client._session._base_url
        self._api_client._session._base_url = None
        result = await self._api_client.get(
            url=response["href"], getter=aiohttp.ClientResponse.read
        )
        self._api_client._session._base_url = save

        return result

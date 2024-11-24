import aiohttp

from cringe_pics_telebot.entities import Image, YandexDiskImagePath
from cringe_pics_telebot.repositories.api_client.api_client import ApiClient


class YandexDiskApiClient:
    BASE_URL = "https://cloud-api.yandex.net"

    def __init__(self, oauth_token: str) -> None:
        self._api_client = ApiClient(base_url=self.BASE_URL, oauth_token=oauth_token)

    async def get_images_paths(self, dirpath: str) -> list[YandexDiskImagePath]:
        params: dict[str, str | int | float] = {
            "fields": ".".join(["_embedded"]),
            "path": f"app:/{dirpath.strip("/")}",
            "limit": 1_000_000,
        }

        response = await self._api_client.get(url="/v1/disk/resources", params=params)

        items: list[YandexDiskImagePath] = []
        for item in response["_embedded"]["items"]:
            if item["type"] == "file" and item["mime_type"].startswith("image"):
                items.append(
                    YandexDiskImagePath(
                        path=item["path"],
                        mime_type=item["mime_type"] if item["mime_type"] else None,
                    )
                )

        return items

    async def download_image(self, image_path: YandexDiskImagePath) -> Image:
        params = {
            "path": image_path.path,
            "fields": ".".join(["href"]),
        }

        response = await self._api_client.get(
            url="/v1/disk/resources/download", params=params
        )

        save = self._api_client._session._base_url
        self._api_client._session._base_url = None
        result = await self._api_client.get(
            url=response["href"], getter=aiohttp.ClientResponse.read
        )
        self._api_client._session._base_url = save

        try:
            image_format = (
                image_path.mime_type.split("/")[-1]
                if image_path.mime_type is not None
                else None
            )
        except IndexError:
            image_format = None

        return Image(data=result, format=image_format)

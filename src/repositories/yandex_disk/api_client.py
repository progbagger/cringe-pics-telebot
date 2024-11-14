import asyncio
import atexit
import aiohttp
import logging

logger = logging.getLogger(__name__)


class YandexDiskApiClient:
    BASE_URL = "https://cloud-api.yandex.net"

    def __init__(self, oauth_token: str) -> None:
        self._oauth_token = oauth_token
        self._session = aiohttp.ClientSession(
            base_url=self.BASE_URL,
            headers={"Authorization", f"OAuth {self._oauth_token}"},
            raise_for_status=True,
        )

        atexit.register(self._sync_close)

    async def _close(self) -> None:
        await self._session.close()

    def _sync_close(self) -> None:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self._close())

    # async def

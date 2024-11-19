import asyncio
import atexit
from typing import Any, Coroutine
import aiohttp
import logging

from yarl import Query

from utilities import setup_logger

logger = setup_logger(
    name=__name__,
    level=logging.INFO,
    fmt="%(asctime)s %(method)s %(status_code)s %(message)s",
)


def log_request(*, method: str, status_code: int, url: str) -> None:
    if 400 > status_code:
        logging_func = logger.info
    else:
        logging_func = logger.error

    logging_func(url, extra={"method": method, "status_code": status_code})


class ApiClient:
    def __init__(self, *, base_url: str, oauth_token: str) -> None:
        self._base_url = base_url
        self._token = oauth_token

        self._session = aiohttp.ClientSession(
            base_url=self.base_url,
            headers={"Authorization": f"OAuth {self.token}"},
        )

        atexit.register(self._sync_close)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def token(self) -> str:
        return self._token

    async def _close(self) -> None:
        await self._session.close()

    def _sync_close(self) -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        loop.run_until_complete(self._close())

        logging.getLogger().info("ApiClient session is closed")

    async def request(
        self,
        *,
        method: str,
        url: str,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        async with self._session.request(
            method=method, url=url, params=params, json=json, headers=headers, **kwargs
        ) as response:
            log_request(
                method=response.method,
                status_code=response.status,
                url=response.url,
            )

            response.raise_for_status()
            return await getter(response)

    async def get(
        self,
        url: str,
        *,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        return await self.request(
            method="GET",
            url=url,
            params=params,
            json=json,
            headers=headers,
            getter=getter,
            **kwargs,
        )

    async def post(
        self,
        url: str,
        *,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        return await self.request(
            method="POST",
            url=url,
            params=params,
            json=json,
            headers=headers,
            getter=getter,
            **kwargs,
        )

    async def put(
        self,
        url: str,
        *,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        return await self.request(
            method="PUT",
            url=url,
            params=params,
            json=json,
            headers=headers,
            getter=getter,
            **kwargs,
        )

    async def head(
        self,
        url: str,
        *,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        return await self.request(
            method="HEAD",
            url=url,
            params=params,
            json=json,
            headers=headers,
            getter=getter,
            **kwargs,
        )

    async def delete(
        self,
        url: str,
        *,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        return await self.request(
            method="DELETE",
            url=url,
            params=params,
            json=json,
            headers=headers,
            getter=getter,
            **kwargs,
        )

    async def patch(
        self,
        url: str,
        *,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        return await self.request(
            method="PATCH",
            url=url,
            params=params,
            json=json,
            headers=headers,
            getter=getter,
            **kwargs,
        )

    async def options(
        self,
        url: str,
        *,
        params: Query | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        getter: Coroutine[None, None, Any] = aiohttp.ClientResponse.json,
        **kwargs,
    ) -> Any:
        return await self.request(
            method="OPTIONS",
            url=url,
            params=params,
            json=json,
            headers=headers,
            getter=getter,
            **kwargs,
        )

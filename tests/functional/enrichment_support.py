import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp
import asyncpg

if TYPE_CHECKING:
    from tests.functional.conftest import FakeTelegramServer


@dataclass(frozen=True, slots=True)
class FakeOllamaServer:
    base_url: str

    async def reset(self) -> None:
        await self._post("reset", {})

    async def configure(self, *, responses: list[dict[str, Any]] | None = None, block: bool = False) -> None:
        await self._post("configure", {"responses": responses or [], "block": block})

    async def release(self) -> None:
        await self._post("release", {})

    async def requests(self, *, wait_for: int | None = None) -> list[dict[str, Any]]:
        path = "requests" if wait_for is None else f"wait?count={wait_for}"
        async with aiohttp.ClientSession() as session, session.get(f"{self.base_url}/test/{path}") as response:
            response.raise_for_status()
            return (await response.json())["result"]

    async def _post(self, path: str, payload: dict[str, Any]) -> None:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.base_url}/test/{path}", json=payload) as response,
        ):
            response.raise_for_status()


@dataclass(slots=True)
class EnrichmentDatabase:
    connection: asyncpg.Connection
    changed: asyncio.Event

    async def jobs(self) -> list[dict[str, Any]]:
        rows = await self.connection.fetch(
            """
            SELECT jobs.id, jobs.media_id, media.source_path, jobs.source_revision, jobs.status::text,
                   jobs.attempt_count, jobs.retry_count, jobs.model, jobs.prompt_sha256, jobs.result_class,
                   jobs.available_at, jobs.leased_until, jobs.lease_token, jobs.updated_at, jobs.last_error
            FROM media_alias_enrichment_jobs AS jobs JOIN category_media AS media ON media.id = jobs.media_id
            ORDER BY media.source_path, jobs.id
            """
        )
        return [dict(row) for row in rows]

    async def wait_for_jobs(
        self,
        predicate: Callable[[list[dict[str, Any]]], bool],
        *,
        timeout: float = 10,
    ) -> list[dict[str, Any]]:
        # LISTEN/NOTIFY observes committed transitions, without time-based polling.
        jobs: list[dict[str, Any]] = []
        try:
            async with asyncio.timeout(timeout):
                while True:
                    self.changed.clear()
                    jobs = await self.jobs()
                    if predicate(jobs):
                        return jobs
                    await self.changed.wait()
        except TimeoutError as error:
            states = [(job["source_path"], job["status"], job["result_class"]) for job in jobs]
            raise TimeoutError(f"Enrichment jobs did not reach expected state; last observed: {states}") from error


@dataclass(slots=True)
class EnrichmentBot:
    process: asyncio.subprocess.Process
    telegram: FakeTelegramServer
    logs: deque[str]
    stop: Callable[[], Awaitable[None]]
    wait_for_log: Callable[[str], Awaitable[None]]

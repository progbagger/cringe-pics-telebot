from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from redis import asyncio as redis

_client: ContextVar[redis.Redis] = ContextVar("_redis_client")


class RedisError(Exception): ...


class RedisConnectionError(RedisError): ...


@asynccontextmanager
async def connect(*, username: str, password: str, host: str, port: int) -> AsyncGenerator[redis.Redis]:
    try:
        _client.get()
        raise RedisConnectionError("Already connected")
    except LookupError:
        pass

    async with redis.from_url(f"redis://{host}:{port}", username=username, password=password) as conn:
        if not await conn.ping():
            raise RedisConnectionError("Failed to connect to Redis server.")

        with _client.set(conn):
            yield conn


@asynccontextmanager
async def get_connection() -> AsyncGenerator[redis.Redis]:
    try:
        yield _client.get()
    except LookupError as e:
        raise RedisConnectionError("Connection unitialized. Run connect() first.") from e

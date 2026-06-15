from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar

from redis import asyncio as redis

_pool: ContextVar[redis.ConnectionPool] = ContextVar("_redis_pool")
_client: ContextVar[redis.Redis] = ContextVar("_redis_client")


class RedisError(Exception): ...


class RedisConnectionError(RedisError): ...


class RedisUninitializedError(RedisError): ...


@asynccontextmanager
async def connect(*, username: str, password: str, host: str, port: int) -> AsyncGenerator[redis.ConnectionPool]:
    with suppress(LookupError):
        _pool.get()
        raise RedisConnectionError("Redis connection pool is already initialized.")

    with _pool.set(
        pool := redis.ConnectionPool(
            host=host,
            port=port,
            username=username,
            password=password,
        )
    ):
        async with redis.Redis(connection_pool=pool) as conn:
            if not await conn.ping():
                raise RedisConnectionError("Failed to connect to Redis server.")

        yield pool
        await _pool.get().disconnect()


def get_pool() -> redis.ConnectionPool:
    try:
        return _pool.get()
    except LookupError as e:
        raise RedisUninitializedError("Redis connection pool is not initialized. Call connect() first.") from e


@asynccontextmanager
async def get_connection() -> AsyncGenerator[redis.Redis]:
    try:
        yield _client.get()
    except LookupError:
        async with redis.Redis(connection_pool=get_pool()) as connection:
            with _client.set(connection):
                yield connection

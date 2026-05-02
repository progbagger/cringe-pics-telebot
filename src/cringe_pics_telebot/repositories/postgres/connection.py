import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ._metadata import _metadata

_session: ContextVar[AsyncSession] = ContextVar("_session")
_sessionmaker: async_sessionmaker | None = None
_engine: AsyncEngine | None = None


class DbConnectionError(ConnectionError): ...


class NotConnectedError(DbConnectionError): ...


class AlreadyConnectedError(DbConnectionError): ...


def connect(
    *,
    username: str,
    password: str,
    database: str,
    port: int,
    host: str,
):
    global _sessionmaker
    if _sessionmaker is not None:
        raise AlreadyConnectedError

    global _engine
    _sessionmaker = async_sessionmaker(
        _engine := create_async_engine(
            url=URL.create(
                drivername="postgresql+asyncpg",
                username=username,
                password=password,
                host=host,
                port=port,
                database=database,
            )
        )
    )


@asynccontextmanager
async def get_connection() -> AsyncGenerator[AsyncSession]:
    try:
        yield _session.get()
    except LookupError as e:
        if _sessionmaker is None:
            raise NotConnectedError from e

        async with _sessionmaker() as session:
            token = _session.set(session)
            try:
                yield session
            finally:
                _session.reset(token)


@asynccontextmanager
async def transaction() -> AsyncGenerator[None]:
    async with get_connection() as conn, conn.begin():
        yield


async def create_tables() -> None:
    if _engine is None:
        raise NotConnectedError

    async with _engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)

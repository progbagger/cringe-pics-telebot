from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_session: ContextVar[AsyncSession] = ContextVar("_session")

_sessionmaker: async_sessionmaker | None = None


class NotConnectedError(Exception): ...


def connect(
    *,
    username: str,
    password: str,
    database: str,
    port: int,
    host: str,
) -> None:
    global _sessionmaker

    _sessionmaker = async_sessionmaker(
        create_async_engine(
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
async def get_connection() -> AsyncIterator[AsyncSession]:
    try:
        yield _session.get()
    except LookupError as e:
        if _sessionmaker is None:
            raise NotConnectedError("Not connected to the database") from e

        async with _sessionmaker() as session:
            token = _session.set(session)
            try:
                yield session
            finally:
                _session.reset(token)

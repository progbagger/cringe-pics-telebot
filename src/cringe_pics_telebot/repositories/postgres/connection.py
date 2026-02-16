from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_session: ContextVar[AsyncSession] = ContextVar("_session")
_sessionmaker: async_sessionmaker | None = None


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
) -> None:
    global _sessionmaker
    if _sessionmaker is not None:
        raise AlreadyConnectedError

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


@asynccontextmanager
async def transaction() -> AsyncIterator[None]:
    async with get_connection() as conn, conn.begin():
        yield

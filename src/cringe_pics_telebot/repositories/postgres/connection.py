from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ._metadata import _metadata

_session: ContextVar[AsyncSession] = ContextVar("_session")
_sessionmaker: ContextVar[async_sessionmaker] = ContextVar("_sessionmaker")
_engine: ContextVar[AsyncEngine] = ContextVar("_asyncpg_engine")


class DbConnectionError(Exception): ...


class NotConnectedError(DbConnectionError): ...


class AlreadyConnectedError(DbConnectionError): ...


@asynccontextmanager
async def connect(
    *,
    username: str,
    password: str,
    database: str,
    port: int,
    host: str,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker]]:
    try:
        _engine.get()
        raise AlreadyConnectedError
    except LookupError:
        pass

    with (
        _engine.set(
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
        ),
        _sessionmaker.set(async_sessionmaker(_engine.get())),
    ):
        yield _engine.get(), _sessionmaker.get()
        await _engine.get().dispose()


def get_engine() -> AsyncEngine:
    try:
        return _engine.get()
    except LookupError as e:
        raise NotConnectedError from e


def get_sessionmaker() -> async_sessionmaker:
    try:
        return _sessionmaker.get()
    except LookupError as e:
        raise NotConnectedError from e


@asynccontextmanager
async def get_connection() -> AsyncGenerator[AsyncSession]:
    try:
        yield _session.get()
    except LookupError:
        async with _sessionmaker.get()() as session:
            with _session.set(session):
                yield session


@asynccontextmanager
async def transaction() -> AsyncGenerator[None]:
    async with get_connection() as conn:
        if conn.in_transaction():
            yield
        else:
            async with conn.begin():
                yield


async def create_tables() -> None:
    try:
        engine = _engine.get()
    except LookupError as e:
        raise NotConnectedError from e

    async with engine.begin() as tx:
        await tx.run_sync(_metadata.create_all)

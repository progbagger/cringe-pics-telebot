from curses import echo
from typing import Any, AsyncGenerator
import pytest
from orm import Base
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncEngine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy import select

from orm import User, Category


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine("sqlite+aiosqlite://", echo=True)
    yield engine
    await engine.dispose()


@pytest.fixture
def session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine)


async def test_create_tables(engine: AsyncEngine, session: AsyncSession):
    # act
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # assert
    async with session() as session:
        assert not (await session.execute(select(User))).all()
        assert not (await session.execute(select(Category))).all()

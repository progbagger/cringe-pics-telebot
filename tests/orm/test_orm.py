from typing import AsyncGenerator
import pytest
from orm import Base
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncEngine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy import select, insert
import asyncio

from orm import User, Category


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine("sqlite+aiosqlite://", echo=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = async_sessionmaker(engine)
    async with sessionmaker() as session:
        yield session


@pytest.fixture
async def create_tables(engine: AsyncEngine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def test_create_tables(create_tables, session: AsyncSession):
    # assert
    async with session.begin():
        users = (await session.execute(select(User))).all()
        categories = (await session.execute(select(Category))).all()

        assert not users
        assert not categories


async def test_fill_tables(create_tables, session: AsyncSession):
    # arrange
    category1 = Category(name="name1", path="path1")
    category2 = Category(name="name2", path="path2")
    category3 = Category(name="name3", path="path3")

    user1 = User(
        categories=[
            category1,
            category2,
        ]
    )
    user2 = User(
        categories=[
            category2,
            category3,
        ]
    )

    # act
    async with session.begin():
        session.add_all([user1, user2])

    # assert
    async with session.begin():
        categories = (await session.execute(select(Category))).scalars().all()
        users = (await session.execute(select(User))).scalars().all()

        assert len(users) == 2
        assert len(categories) == 3

        user1, user2 = users

        assert user1.categories == [category1, category2]
        assert user2.categories == [category2, category3]

        category1, category2, category3 = categories

        assert category1.name == "name1"
        assert category1.path == "path1"
        assert category1.users == [user1]

        assert category2.name == "name2"
        assert category2.path == "path2"
        assert category2.users == [user1, user2]

        assert category3.name == "name3"
        assert category3.path == "path3"
        assert category3.users == [user2]

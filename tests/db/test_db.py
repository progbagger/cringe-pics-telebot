import asyncio
import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


from cringe_pics_telebot.db import DatabaseManager, NoSuchUserError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker
from cringe_pics_telebot.orm import User, Category
from typing import Sequence


async def test_get_all_categories(engine: AsyncEngine, session: AsyncSession):
    # arrange
    manager = DatabaseManager(async_sessionmaker(engine))
    async with session, manager.session():
        categories = [
            Category(name=f"name{i}", path=f"path{i}", time=datetime.time())
            for i in range(10)
        ]
        async with session.begin():
            session.add_all(categories)
        async with asyncio.TaskGroup() as tg:
            for category in categories:
                tg.create_task(session.refresh(category))

        # act & assert
        result = await manager.get_all_categories()
        assert result == categories


@pytest.mark.parametrize(
    ["db_users", "id", "expected_user_id"],
    [
        ([User(id=1)], 1, 1),
        ([User(id=1)], 2, None),
        ([], 1, None),
    ],
)
async def test_get_user_by_id(
    engine: AsyncEngine,
    session: AsyncSession,
    db_users: Sequence[User],
    id: int,
    expected_user_id: int | None,
):
    # arrange
    manager = DatabaseManager(async_sessionmaker(engine))

    async with session, manager.session():
        async with session.begin():
            session.add_all(db_users)

        async with asyncio.TaskGroup() as tg:
            for db_user in db_users:
                tg.create_task(session.refresh(db_user))
            if expected_user_id is not None:
                expected_user_task = tg.create_task(
                    session.execute(select(User).where(User.id == expected_user_id))
                )

        if expected_user_id is not None:
            expected_user = expected_user_task.result().scalar_one()
        else:
            expected_user = None

        # act & assert
        if expected_user is None:
            with pytest.raises(NoSuchUserError):
                await manager.get_user_by_id(id)
        else:
            assert await manager.get_user_by_id(id) == expected_user


@pytest.mark.parametrize(
    ["db_users", "user_to_insert", "expected_db_users_ids"],
    [
        ([], User(id=1), [1]),
        ([User(id=1)], User(id=2), [1, 2]),
    ],
)
async def test_add_user(
    engine: AsyncEngine,
    session: AsyncSession,
    db_users: Sequence[User],
    user_to_insert: User,
    expected_db_users_ids: Sequence[int],
):
    # arrange
    manager = DatabaseManager(async_sessionmaker(engine))
    async with session, manager.session() as m_session:
        async with session.begin():
            session.add_all(db_users)

        async with asyncio.TaskGroup() as tg:
            for db_user in db_users:
                tg.create_task(session.refresh(db_user))

        # act & assert
        async with m_session.begin():
            await manager.add_user(user_to_insert)
            return

        current_db_users = (await m_session.execute(select(User))).scalars().all()
        expected_db_users = (
            (
                await session.execute(
                    select(User).where(User.id.in_(expected_db_users_ids))
                )
            )
            .scalars()
            .all()
        )

        assert expected_db_users == current_db_users


@pytest.mark.parametrize(
    ["db_users", "user_to_insert"],
    [
        ([User(id=1)], User(id=1)),
    ],
)
async def test_add_user__unique_violated(
    engine: AsyncEngine,
    session: AsyncSession,
    db_users: Sequence[User],
    user_to_insert: User,
):
    # arrange
    manager = DatabaseManager(async_sessionmaker(engine))
    async with session, manager.session() as m_session:
        async with session.begin():
            session.add_all(db_users)

        async with asyncio.TaskGroup() as tg:
            for db_user in db_users:
                tg.create_task(session.refresh(db_user))

        # act & assert
        with pytest.raises(IntegrityError):
            async with m_session.begin():
                await manager.add_user(user_to_insert)

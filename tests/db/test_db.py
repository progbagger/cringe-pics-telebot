import datetime

import pytest


from cringe_pics_telebot.db import get_users_by_category, get_categories
from sqlalchemy.ext.asyncio import AsyncSession
from cringe_pics_telebot.orm import User, Category
from sqlalchemy import select


async def test_get_categories(session: AsyncSession):
    # arrange
    async with session.begin():
        session.add_all(
            [
                Category(name="name1", path="path1", time=datetime.time()),
                Category(name="name2", path="path2", time=datetime.time()),
                Category(name="name3", path="path3", time=datetime.time()),
            ]
        )

    # act
    result = await get_categories(session)

    # assert
    result = sorted(result, key=lambda v: v.name)

    for cat, name in zip(result, ["name1", "name2", "name3"]):
        assert cat.name == name


async def test_get_users_by_category(session: AsyncSession):
    # arrange
    categories = [
        Category(name="name1", path="path1", time=datetime.time()),
        Category(name="name2", path="path2", time=datetime.time()),
    ]
    users = [
        User(id=1, categories=[categories[0]]),
        User(id=2, categories=[categories[1]]),
        User(id=3, categories=categories),
    ]

    async with session.begin():
        session.add_all(users)

    # act
    result1 = await get_users_by_category(session=session, category=categories[0])
    result2 = await get_users_by_category(session=session, category=categories[1])

    # assert
    assert [users[0].id, users[2].id] == sorted(user.id for user in result1)
    assert [users[1].id, users[2].id] == sorted(user.id for user in result2)

from typing import Sequence
from cringe_pics_telebot.orm import Category, User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select


async def get_categories(session: AsyncSession) -> Sequence[Category]:
    async with session.begin():
        return (await session.execute(select(Category))).scalars().all()


async def get_users_by_category(
    *, session: AsyncSession, category: Category
) -> Sequence[User]:
    async with session.begin():
        result = (
            (
                await session.execute(
                    select(User)
                    .join(User.categories)
                    .where(Category.id.in_([category.id]))
                )
            )
            .scalars()
            .all()
        )

        return result

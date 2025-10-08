from contextlib import asynccontextmanager
from typing import AsyncGenerator, Sequence
from cringe_pics_telebot.orm import Category, User
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.exc import NoResultFound
from sqlalchemy import select


class DbError(Exception):
    pass


class NoSessionError(DbError):
    pass


class EmptyResultError(DbError):
    pass


class NoSuchUserError(EmptyResultError):
    pass


class DatabaseManager:
    """Entity to properly and safely interact with ORM entities

    Basic usage:
    >>> manager = DatabaseManager(async_sessionmaker(engine))
    >>> async with manager.begin():
    >>>     manager.add_user(User(id=123))

    In case if sqlalchemy session is needed:
    >>> async with DatabaseManager(async_sessionmaker(engine)) as manager:
    >>>     await manager.session.execute(select(User))
    """

    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self.__sessionmaker = sessionmaker
        self._session: AsyncSession | None = None

    @property
    def __session(self) -> AsyncSession:
        if self._session is not None:
            return self._session

        raise NoSessionError("Session is not detected")

    async def get_all_categories(self) -> Sequence[Category]:
        return (await self.__session.execute(select(Category))).scalars().all()

    async def get_all_users(self) -> Sequence[User]:
        return (await self.__session.execute(select(Category))).scalars().all()

    async def get_user_by_id(self, id: int) -> User:
        try:
            return (
                await self.__session.execute(select(User).where(User.id == id))
            ).scalar_one()
        except NoResultFound:
            raise NoSuchUserError("No user with id %s was found", id)

    async def add_user(self, user: User) -> None:
        self.__session.add(user)

    async def get_category(self, category_name: str) -> Category | None:
        return (
            await self.__session.execute(
                select(Category).where(Category.name == category_name)
            )
        ).scalar_one_or_none()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self as s:
            yield s

    async def __aenter__(self) -> AsyncSession:
        if self._session is None:
            self._session = self.__sessionmaker()

        return self._session

    async def __aexit__(self, *args, **kwargs) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

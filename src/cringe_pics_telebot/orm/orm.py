import datetime
from dataclasses import dataclass

from sqlalchemy import Column, ForeignKey, Table, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(AsyncAttrs, DeclarativeBase):
    pass


users_to_categories_table = Table(
    "users_to_categories",
    Base.metadata,
    Column("user_id", ForeignKey("users.id")),
    Column("category_id", ForeignKey("categories.id")),
)


@dataclass(kw_only=True)
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    categories: Mapped[list["Category"]] = relationship(
        "Category",
        secondary=users_to_categories_table,
        back_populates="users",
        lazy="immediate",
    )


@dataclass(kw_only=True)
class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column()
    path: Mapped[str] = mapped_column()
    time: Mapped[datetime.time] = mapped_column()
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    users: Mapped[list[User]] = relationship(
        User,
        secondary=users_to_categories_table,
        back_populates="categories",
        lazy="immediate",
    )

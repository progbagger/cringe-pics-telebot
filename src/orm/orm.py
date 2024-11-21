import datetime
from sqlalchemy import Column, ForeignKey, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncAttrs


class Base(AsyncAttrs, DeclarativeBase):
    pass


users_to_categories_table = Table(
    "users_to_categories",
    Base.metadata,
    Column("user_id", ForeignKey("users.id")),
    Column("category_id", ForeignKey("categories.id")),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    categories: Mapped[list["Category"]] = relationship(
        "Category",
        secondary=users_to_categories_table,
        back_populates="users",
        lazy="selectin",
    )

    def __eq__(self, other: "User") -> bool:
        return self.id == other.id


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]
    path: Mapped[str]

    users: Mapped[list[User]] = relationship(
        User,
        secondary=users_to_categories_table,
        back_populates="categories",
        lazy="selectin",
    )

    def __eq__(self, other: "Category") -> bool:
        return self.id == other.id

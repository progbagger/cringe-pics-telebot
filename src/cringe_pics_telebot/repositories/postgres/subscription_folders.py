from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert

from cringe_pics_telebot.entities.admin_subscription_folders import AdminSubscriptionFolderCategory
from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.subscription_menu import (
    SubscriptionFolder,
    SubscriptionMenuEntry,
)
from cringe_pics_telebot.entities.subscription_schedule import SubscriptionScheduleKind
from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo

from .connection import get_connection
from .tables import (
    subscription_category_folder_members,
    subscription_category_folders,
    subscription_types,
    subscriptions,
)

folders = subscription_category_folders
members = subscription_category_folder_members
st = subscription_types
s = subscriptions


async def get_all_subscription_folders() -> list[SubscriptionFolder]:
    query = select(folders.c.id, folders.c.name).order_by(func.lower(folders.c.name), folders.c.id)
    async with get_connection() as conn:
        rows = (await conn.execute(query)).all()

    return [SubscriptionFolder(id=row.id, name=row.name) for row in rows]


async def get_admin_subscription_folder_categories() -> list[AdminSubscriptionFolderCategory]:
    query = (
        select(
            st.c.id,
            st.c.name,
            folders.c.id.label("folder_id"),
            folders.c.name.label("folder_name"),
        )
        .select_from(
            st.outerjoin(members, members.c.subscription_type_id == st.c.id).outerjoin(
                folders,
                folders.c.id == members.c.folder_id,
            )
        )
        .order_by(func.lower(st.c.name), st.c.id)
    )
    async with get_connection() as conn:
        rows = (await conn.execute(query)).all()

    return [
        AdminSubscriptionFolderCategory(
            id=row.id,
            name=row.name,
            folder=(SubscriptionFolder(id=row.folder_id, name=row.folder_name) if row.folder_id is not None else None),
        )
        for row in rows
    ]


async def get_user_subscription_menu_entries(user_id: int) -> list[SubscriptionMenuEntry]:
    subscribed = exists(
        select(s.c.id).where(
            s.c.user_id == user_id,
            s.c.subscription_type_id == st.c.id,
        )
    ).label("subscribed")
    query = (
        select(
            st.c.id,
            st.c.name,
            st.c.time,
            st.c.weekdays,
            st.c.schedule_kind,
            st.c.annual_month,
            st.c.annual_day,
            subscribed,
            folders.c.id.label("folder_id"),
            folders.c.name.label("folder_name"),
        )
        .select_from(st.outerjoin(members, members.c.subscription_type_id == st.c.id).outerjoin(folders))
        .where(st.c.is_active.is_(True), st.c.time.is_not(None))
    )

    async with get_connection() as conn:
        rows = (await conn.execute(query)).all()

    return [
        SubscriptionMenuEntry(
            subscription=SubscriptionInfo(
                id=row.id,
                name=row.name,
                send_time=row.time,
                weekdays=SubscriptionWeekdays.from_mask(row.weekdays),
                subscribed=row.subscribed,
                schedule_kind=SubscriptionScheduleKind(row.schedule_kind),
                annual_date=(
                    AnnualDate(month=row.annual_month, day=row.annual_day)
                    if row.annual_month is not None and row.annual_day is not None
                    else None
                ),
            ),
            folder=(SubscriptionFolder(id=row.folder_id, name=row.folder_name) if row.folder_id is not None else None),
        )
        for row in rows
    ]


async def get_subscription_folder(
    folder_id: int,
    *,
    with_for_update: bool = False,
) -> SubscriptionFolder | None:
    query = select(folders.c.id, folders.c.name).where(folders.c.id == folder_id)
    if with_for_update:
        query = query.with_for_update()

    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()

    return SubscriptionFolder(id=row.id, name=row.name) if row is not None else None


async def get_subscription_folder_by_name(name: str) -> SubscriptionFolder | None:
    query = select(folders.c.id, folders.c.name).where(folders.c.name == name)
    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()

    return SubscriptionFolder(id=row.id, name=row.name) if row is not None else None


async def create_subscription_folder(name: str) -> SubscriptionFolder | None:
    query = (
        insert(folders)
        .values(name=name)
        .on_conflict_do_nothing(index_elements=[folders.c.name])
        .returning(folders.c.id, folders.c.name)
    )
    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()

    return SubscriptionFolder(id=row.id, name=row.name) if row is not None else None


async def update_subscription_folder_name(folder_id: int, name: str) -> SubscriptionFolder | None:
    query = (
        update(folders)
        .where(folders.c.id == folder_id)
        .values(name=name, updated_at=func.now())
        .returning(folders.c.id, folders.c.name)
    )
    async with get_connection() as conn:
        row = (await conn.execute(query)).one_or_none()

    return SubscriptionFolder(id=row.id, name=row.name) if row is not None else None


async def delete_subscription_folder(folder_id: int) -> bool:
    async with get_connection() as conn:
        deleted_id = await conn.scalar(delete(folders).where(folders.c.id == folder_id).returning(folders.c.id))

    return deleted_id is not None


async def get_subscription_type_folder_id(
    subscription_type_id: int,
    *,
    with_for_update: bool = False,
) -> int | None:
    query = select(members.c.folder_id).where(members.c.subscription_type_id == subscription_type_id)
    if with_for_update:
        query = query.with_for_update()

    async with get_connection() as conn:
        return await conn.scalar(query)


async def add_subscription_type_to_folder(*, folder_id: int, subscription_type_id: int) -> bool:
    query = (
        insert(members)
        .values(folder_id=folder_id, subscription_type_id=subscription_type_id)
        .on_conflict_do_nothing()
        .returning(members.c.subscription_type_id)
    )
    async with get_connection() as conn:
        created_id = await conn.scalar(query)

    return created_id is not None


async def remove_subscription_type_from_folder(*, folder_id: int, subscription_type_id: int) -> bool:
    query = (
        delete(members)
        .where(
            members.c.folder_id == folder_id,
            members.c.subscription_type_id == subscription_type_id,
        )
        .returning(members.c.subscription_type_id)
    )
    async with get_connection() as conn:
        deleted_id = await conn.scalar(query)

    return deleted_id is not None


async def get_active_scheduled_folder_subscription_type_ids(
    folder_id: int,
    *,
    with_for_update: bool = False,
) -> tuple[int, ...]:
    query = (
        select(st.c.id)
        .select_from(st.join(members, members.c.subscription_type_id == st.c.id))
        .where(
            members.c.folder_id == folder_id,
            st.c.is_active.is_(True),
            st.c.time.is_not(None),
        )
        .order_by(st.c.id)
    )
    if with_for_update:
        query = query.with_for_update()

    async with get_connection() as conn:
        return tuple((await conn.scalars(query)).all())

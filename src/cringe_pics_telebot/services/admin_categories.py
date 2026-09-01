import re
from collections.abc import Iterable
from datetime import datetime, time

from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.repositories.postgres import (
    CreateSubscriptionType,
    SubscriptionType,
    create_subscription_type,
    get_subscription_type_by_name,
    set_subscription_type_activity,
    transaction,
    update_subscription_type_time,
    update_subscription_type_weekdays,
)

_LOCAL_TIME_PATTERN = re.compile(r"\d{2}:\d{2}")


class InvalidAdminCategoryNameError(ValueError): ...


class InvalidAdminCategoryPathError(ValueError): ...


class InvalidAdminCategoryTimeError(ValueError): ...


class AdminCategoryNameConflictError(ValueError): ...


def parse_admin_category_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise InvalidAdminCategoryNameError("Category name must not be empty")
    return name


def parse_admin_category_path(value: str) -> str:
    path = value.strip()
    if not path:
        raise InvalidAdminCategoryPathError("Category media directory path must not be empty")
    return path


def parse_admin_category_time(value: str) -> time:
    normalized = value.strip()
    if _LOCAL_TIME_PATTERN.fullmatch(normalized) is None:
        raise InvalidAdminCategoryTimeError("Category time must use HH:MM")

    try:
        return datetime.strptime(normalized, "%H:%M").time()
    except ValueError as error:
        raise InvalidAdminCategoryTimeError("Category time contains an invalid hour or minute") from error


async def admin_category_name_exists(name: str) -> bool:
    return await get_subscription_type_by_name(name) is not None


async def create_admin_category(data: CreateSubscriptionType) -> SubscriptionType:
    async with transaction():
        category = await create_subscription_type(data)
    if category is None:
        raise AdminCategoryNameConflictError(data.name)
    return category


async def set_admin_category_time(
    category_id: int,
    send_time: time | None,
) -> SubscriptionType | None:
    async with transaction():
        return await update_subscription_type_time(category_id, send_time)


async def set_admin_category_weekdays(
    category_id: int,
    weekdays: Iterable[int],
) -> SubscriptionType | None:
    normalized_weekdays = SubscriptionWeekdays(*weekdays)
    async with transaction():
        return await update_subscription_type_weekdays(category_id, normalized_weekdays)


async def set_admin_category_activity(
    category_id: int,
    *,
    is_active: bool,
) -> SubscriptionType | None:
    async with transaction():
        return await set_subscription_type_activity(category_id, is_active=is_active)

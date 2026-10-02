from collections import defaultdict

from cringe_pics_telebot.entities.subscription_menu import (
    SubscriptionFolderMenu,
    UserSubscriptionMenu,
)
from cringe_pics_telebot.entities.subscriptions import SubscriptionInfo
from cringe_pics_telebot.repositories.postgres import (
    create_subscriptions,
    delete_subscriptions,
    get_active_scheduled_folder_subscription_type_ids,
    get_active_scheduled_subscription_type,
    get_active_scheduled_subscription_types,
    get_active_subscription_types,
    get_subscription_folder,
    get_subscription_type_folder_id,
    get_user_subscription_menu_entries,
    transaction,
)
from cringe_pics_telebot.repositories.postgres import get_subscription_users as get_subscription_users_from_pg
from cringe_pics_telebot.repositories.postgres import (
    get_user_birthday as get_user_birthday_from_pg,
)
from cringe_pics_telebot.repositories.postgres import get_user_subscriptions as get_user_subscriptions_from_pg
from cringe_pics_telebot.repositories.postgres.entities import CreateSubscription, User
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import SubscriptionType
from cringe_pics_telebot.repositories.postgres.users import create_user


class SubscriptionTypeUnavailableError(LookupError): ...


class SubscriptionMenuLocationUnavailableError(LookupError): ...


class SubscriptionFolderUnavailableError(LookupError): ...


async def get_subscription_types() -> list[SubscriptionType]:
    return await get_active_subscription_types()


async def get_scheduled_subscription_types() -> list[SubscriptionType]:
    return await get_active_scheduled_subscription_types()


async def get_user_subscriptions(user_id: int) -> list[SubscriptionInfo]:
    return await get_user_subscriptions_from_pg(user_id)


async def get_user_subscription_menu(user_id: int) -> UserSubscriptionMenu:
    entries = await get_user_subscription_menu_entries(user_id)
    grouped_subscriptions: defaultdict[int, list[SubscriptionInfo]] = defaultdict(list)
    folders = {}
    ungrouped_subscriptions = []

    for entry in entries:
        if entry.folder is None:
            ungrouped_subscriptions.append(entry.subscription)
            continue

        folders[entry.folder.id] = entry.folder
        grouped_subscriptions[entry.folder.id].append(entry.subscription)

    return UserSubscriptionMenu(
        folders={
            folder_id: SubscriptionFolderMenu(
                folder=folder,
                subscriptions=tuple(grouped_subscriptions[folder_id]),
            )
            for folder_id, folder in folders.items()
        },
        ungrouped_subscriptions=tuple(ungrouped_subscriptions),
    )


async def user_has_birthday(user_id: int) -> bool:
    return await get_user_birthday_from_pg(user_id) is not None


async def get_subscription_users(subscription_type_id: int) -> list[User]:
    return await get_subscription_users_from_pg(subscription_type_id)


async def subscribe(*, user_id: int, subscription_type_id: int) -> None:
    async with transaction():
        if await get_active_scheduled_subscription_type(subscription_type_id, with_for_update=True) is None:
            raise SubscriptionTypeUnavailableError(subscription_type_id)

        await create_user(user_id)
        await create_subscriptions(
            (
                CreateSubscription(
                    subscription_type_id=subscription_type_id,
                    user_id=user_id,
                ),
            )
        )


async def subscribe_from_menu(
    *,
    user_id: int,
    subscription_type_id: int,
    folder_id: int | None,
) -> None:
    async with transaction():
        await _require_subscription_menu_location(
            subscription_type_id=subscription_type_id,
            folder_id=folder_id,
        )
        await create_user(user_id)
        await create_subscriptions(
            (
                CreateSubscription(
                    subscription_type_id=subscription_type_id,
                    user_id=user_id,
                ),
            )
        )


async def unsubscribe(*, user_id: int, subscription_type_id: int) -> None:
    async with transaction():
        await delete_subscriptions(
            user_id=user_id,
            subscription_type_ids=(subscription_type_id,),
        )


async def unsubscribe_from_menu(
    *,
    user_id: int,
    subscription_type_id: int,
    folder_id: int | None,
) -> None:
    async with transaction():
        await _require_subscription_menu_location(
            subscription_type_id=subscription_type_id,
            folder_id=folder_id,
        )
        await delete_subscriptions(
            user_id=user_id,
            subscription_type_ids=(subscription_type_id,),
        )


async def set_folder_subscriptions(*, user_id: int, folder_id: int, subscribe: bool) -> None:
    async with transaction():
        if await get_subscription_folder(folder_id, with_for_update=True) is None:
            raise SubscriptionFolderUnavailableError(folder_id)

        subscription_type_ids = await get_active_scheduled_folder_subscription_type_ids(
            folder_id,
            with_for_update=True,
        )
        if not subscription_type_ids:
            raise SubscriptionFolderUnavailableError(folder_id)

        if subscribe:
            await create_user(user_id)
            await create_subscriptions(
                tuple(
                    CreateSubscription(
                        subscription_type_id=subscription_type_id,
                        user_id=user_id,
                    )
                    for subscription_type_id in subscription_type_ids
                )
            )
        else:
            await delete_subscriptions(
                user_id=user_id,
                subscription_type_ids=subscription_type_ids,
            )


async def _require_subscription_menu_location(
    *,
    subscription_type_id: int,
    folder_id: int | None,
) -> None:
    if await get_active_scheduled_subscription_type(subscription_type_id, with_for_update=True) is None:
        raise SubscriptionMenuLocationUnavailableError(subscription_type_id)

    actual_folder_id = await get_subscription_type_folder_id(
        subscription_type_id,
        with_for_update=True,
    )
    if actual_folder_id != folder_id:
        raise SubscriptionMenuLocationUnavailableError(subscription_type_id)

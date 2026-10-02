from cringe_pics_telebot.entities.admin_subscription_folders import AdminSubscriptionFolderEditor
from cringe_pics_telebot.entities.subscription_menu import SubscriptionFolder
from cringe_pics_telebot.repositories.postgres import (
    add_subscription_type_to_folder,
    create_subscription_folder,
    delete_subscription_folder,
    get_admin_subscription_folder_categories,
    get_all_subscription_folders,
    get_subscription_folder,
    get_subscription_folder_by_name,
    get_subscription_type,
    get_subscription_type_folder_id,
    remove_subscription_type_from_folder,
    transaction,
    update_subscription_folder_name,
)


class InvalidAdminSubscriptionFolderNameError(ValueError): ...


class AdminSubscriptionFolderNameConflictError(ValueError): ...


class AdminSubscriptionFolderUnavailableError(LookupError): ...


class AdminSubscriptionFolderCategoryUnavailableError(LookupError): ...


class AdminSubscriptionFolderMembershipConflictError(ValueError):
    def __init__(self, folder: SubscriptionFolder) -> None:
        self.folder = folder
        super().__init__(folder.name)


def parse_admin_subscription_folder_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise InvalidAdminSubscriptionFolderNameError("Subscription folder name must not be empty")
    return name


async def get_admin_subscription_folders() -> list[SubscriptionFolder]:
    return await get_all_subscription_folders()


async def get_admin_subscription_folder_editor(folder_id: int) -> AdminSubscriptionFolderEditor | None:
    folder = await get_subscription_folder(folder_id)
    if folder is None:
        return None

    categories = await get_admin_subscription_folder_categories()
    return AdminSubscriptionFolderEditor(folder=folder, categories=tuple(categories))


async def create_admin_subscription_folder(value: str) -> SubscriptionFolder:
    name = parse_admin_subscription_folder_name(value)
    async with transaction():
        folder = await create_subscription_folder(name)
    if folder is None:
        raise AdminSubscriptionFolderNameConflictError(name)
    return folder


async def rename_admin_subscription_folder(folder_id: int, value: str) -> SubscriptionFolder:
    name = parse_admin_subscription_folder_name(value)
    async with transaction():
        folder = await get_subscription_folder(folder_id, with_for_update=True)
        if folder is None:
            raise AdminSubscriptionFolderUnavailableError(folder_id)

        conflicting_folder = await get_subscription_folder_by_name(name)
        if conflicting_folder is not None and conflicting_folder.id != folder_id:
            raise AdminSubscriptionFolderNameConflictError(name)

        renamed = await update_subscription_folder_name(folder_id, name)
        if renamed is None:
            raise AdminSubscriptionFolderUnavailableError(folder_id)

    return renamed


async def toggle_admin_subscription_folder_category(*, folder_id: int, category_id: int) -> bool:
    async with transaction():
        if await get_subscription_folder(folder_id, with_for_update=True) is None:
            raise AdminSubscriptionFolderUnavailableError(folder_id)
        if await get_subscription_type(category_id, with_for_update=True) is None:
            raise AdminSubscriptionFolderCategoryUnavailableError(category_id)

        current_folder_id = await get_subscription_type_folder_id(category_id, with_for_update=True)
        if current_folder_id == folder_id:
            await remove_subscription_type_from_folder(
                folder_id=folder_id,
                subscription_type_id=category_id,
            )
            return False

        if current_folder_id is not None:
            conflicting_folder = await get_subscription_folder(current_folder_id)
            if conflicting_folder is None:
                raise AdminSubscriptionFolderCategoryUnavailableError(category_id)
            raise AdminSubscriptionFolderMembershipConflictError(conflicting_folder)

        if not await add_subscription_type_to_folder(
            folder_id=folder_id,
            subscription_type_id=category_id,
        ):
            raise AdminSubscriptionFolderCategoryUnavailableError(category_id)

    return True


async def delete_admin_subscription_folder(folder_id: int) -> SubscriptionFolder:
    async with transaction():
        folder = await get_subscription_folder(folder_id, with_for_update=True)
        if folder is None:
            raise AdminSubscriptionFolderUnavailableError(folder_id)
        if not await delete_subscription_folder(folder_id):
            raise AdminSubscriptionFolderUnavailableError(folder_id)

    return folder

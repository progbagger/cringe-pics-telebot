from dataclasses import dataclass

from .subscriptions import SubscriptionInfo


@dataclass(frozen=True, slots=True)
class SubscriptionFolder:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class SubscriptionMenuEntry:
    subscription: SubscriptionInfo
    folder: SubscriptionFolder | None


@dataclass(frozen=True, slots=True)
class SubscriptionFolderMenu:
    folder: SubscriptionFolder
    subscriptions: tuple[SubscriptionInfo, ...]


@dataclass(frozen=True, slots=True)
class UserSubscriptionMenu:
    folders: dict[int, SubscriptionFolderMenu]
    ungrouped_subscriptions: tuple[SubscriptionInfo, ...]

    @property
    def subscriptions(self) -> tuple[SubscriptionInfo, ...]:
        return (
            *self.ungrouped_subscriptions,
            *(subscription for folder in self.folders.values() for subscription in folder.subscriptions),
        )

    def find_folder(self, folder_id: int) -> SubscriptionFolderMenu | None:
        return self.folders.get(folder_id)

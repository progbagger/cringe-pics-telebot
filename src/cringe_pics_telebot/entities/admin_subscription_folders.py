from dataclasses import dataclass

from .subscription_menu import SubscriptionFolder


@dataclass(frozen=True, slots=True)
class AdminSubscriptionFolderCategory:
    id: int
    name: str
    folder: SubscriptionFolder | None


@dataclass(frozen=True, slots=True)
class AdminSubscriptionFolderEditor:
    folder: SubscriptionFolder
    categories: tuple[AdminSubscriptionFolderCategory, ...]

    @property
    def member_count(self) -> int:
        return sum(category.folder is not None and category.folder.id == self.folder.id for category in self.categories)

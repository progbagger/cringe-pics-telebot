from enum import StrEnum

from aiogram.filters.callback_data import CallbackData

_PAGE_PAIR_BASE = 1_000_000


class SubscriptionFolderAction(StrEnum):
    open = "o"
    page = "p"
    subscribe_all = "a"
    unsubscribe_all = "u"
    back = "b"


class SubscriptionCallbackData(CallbackData, prefix="subscription"):
    """Legacy callback data kept for messages sent before keyboard pagination."""

    category_id: int
    subscribe: bool


class SubscriptionActionCallbackData(CallbackData, prefix="subscription_action"):
    category_id: int
    subscribe: bool
    page: int


class SubscriptionPageCallbackData(CallbackData, prefix="subscription_page"):
    page: int


class SubscriptionMenuActionCallbackData(CallbackData, prefix="s"):
    category_id: int
    subscribe: bool
    folder_id: int = 0
    pages: int = 0


class SubscriptionFolderCallbackData(CallbackData, prefix="f"):
    action: SubscriptionFolderAction
    folder_id: int
    pages: int = 0


def pack_subscription_pages(*, root_page: int, folder_page: int) -> int:
    if not 0 <= root_page < _PAGE_PAIR_BASE or not 0 <= folder_page < _PAGE_PAIR_BASE:
        raise ValueError(f"Subscription page must be in range 0..{_PAGE_PAIR_BASE - 1}")

    return root_page * _PAGE_PAIR_BASE + folder_page


def unpack_subscription_pages(pages: int) -> tuple[int, int]:
    if pages < 0:
        raise ValueError("Packed subscription pages must be non-negative")

    return divmod(pages, _PAGE_PAIR_BASE)

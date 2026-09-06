from aiogram.filters.callback_data import CallbackData


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

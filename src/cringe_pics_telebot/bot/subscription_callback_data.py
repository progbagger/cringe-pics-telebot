from aiogram.filters.callback_data import CallbackData


class SubscriptionCallbackData(CallbackData, prefix="subscription"):
    category_id: int
    subscribe: bool

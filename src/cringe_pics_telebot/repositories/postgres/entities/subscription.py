from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class CreateSubscription:
    subscription_type_id: int
    """ID типа подписки"""
    user_id: int
    """ID пользователя, чья это подписка"""
    created_at: datetime
    """Время создания подписки"""


@dataclass(slots=True)
class Subscription(CreateSubscription):
    id: int
    """ID подписки"""

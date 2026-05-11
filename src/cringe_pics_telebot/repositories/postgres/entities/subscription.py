from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True, kw_only=True)
class CreateSubscription:
    subscription_type_id: int
    """ID типа подписки"""
    user_id: int
    """ID пользователя, чья это подписка"""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """Время создания подписки"""


@dataclass(slots=True)
class Subscription(CreateSubscription):
    id: int
    """ID подписки"""

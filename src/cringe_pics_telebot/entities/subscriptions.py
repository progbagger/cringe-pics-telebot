from datetime import time

from attr import dataclass

from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays


@dataclass
class SubscriptionInfo:
    id: int
    """ID типа подписки"""
    name: str
    """Название подписки"""
    send_time: time
    """Время отправки подписки"""
    weekdays: SubscriptionWeekdays
    """ISO-дни недели отправки подписки"""
    subscribed: bool
    """Активна ли подписка"""


@dataclass
class UserSubscriptionsInfo:
    user_id: int
    """ID пользователя"""
    subscriptions: list[SubscriptionInfo]
    """Подписки пользователя"""

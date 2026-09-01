from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays

_WEEKDAY_ABBREVIATIONS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def format_subscription_weekdays(
    weekdays: SubscriptionWeekdays,
    *,
    daily_label: str = "ежедневно",
) -> str:
    if len(weekdays) == len(_WEEKDAY_ABBREVIATIONS):
        return daily_label
    return ", ".join(_WEEKDAY_ABBREVIATIONS[day - 1] for day in weekdays)

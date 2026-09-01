_MIN_ISO_WEEKDAY = 1
_MAX_ISO_WEEKDAY = 7
_DAILY_MASK = (1 << _MAX_ISO_WEEKDAY) - 1


class SubscriptionWeekdays(tuple[int, ...]):
    def __new__(cls, *days: int) -> SubscriptionWeekdays:
        ordered_days = tuple(sorted(days))
        if not ordered_days:
            raise ValueError("Subscription weekdays must not be empty")
        if any(day < _MIN_ISO_WEEKDAY or day > _MAX_ISO_WEEKDAY for day in ordered_days):
            raise ValueError("Subscription weekdays must contain only ISO weekdays from 1 to 7")
        if len(set(ordered_days)) != len(ordered_days):
            raise ValueError("Subscription weekdays must not contain duplicates")

        return super().__new__(cls, ordered_days)

    @property
    def days(self) -> tuple[int, ...]:
        return tuple(self)

    @classmethod
    def daily(cls) -> SubscriptionWeekdays:
        return cls(*range(_MIN_ISO_WEEKDAY, _MAX_ISO_WEEKDAY + 1))

    @classmethod
    def from_mask(cls, mask: int) -> SubscriptionWeekdays:
        if not _MIN_ISO_WEEKDAY <= mask <= _DAILY_MASK:
            raise ValueError("Subscription weekdays mask must be between 1 and 127")
        return cls(*(day for day in range(_MIN_ISO_WEEKDAY, _MAX_ISO_WEEKDAY + 1) if mask & (1 << (day - 1))))

    @property
    def mask(self) -> int:
        return sum(1 << (day - 1) for day in self)

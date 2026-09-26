import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from cringe_pics_telebot.repositories.postgres.administrators import get_administrator_ids
from cringe_pics_telebot.repositories.postgres.entities.subscription_type import SubscriptionType
from cringe_pics_telebot.repositories.postgres.subscription_types import get_active_subscription_types

MAIN_KEYBOARD_TTL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class MainKeyboardSnapshot:
    categories: tuple[SubscriptionType, ...]
    administrator_ids: frozenset[int]


class MainKeyboardCache:
    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._snapshot: MainKeyboardSnapshot | None = None
        self._expires_at = 0.0

    async def get_snapshot(self) -> MainKeyboardSnapshot:
        if self._snapshot is not None and self._clock() < self._expires_at:
            return self._snapshot

        async with self._lock:
            # A concurrent sender may have filled the cache while we waited.
            if self._snapshot is not None and self._clock() < self._expires_at:
                return self._snapshot

            # Anchor expiry to the read, rather than extending it on every send.
            expires_at = self._clock() + MAIN_KEYBOARD_TTL_SECONDS
            categories = await get_active_subscription_types()
            administrator_ids = await get_administrator_ids()

            snapshot = MainKeyboardSnapshot(tuple(categories), administrator_ids)
            self._snapshot = snapshot
            self._expires_at = expires_at

            return snapshot

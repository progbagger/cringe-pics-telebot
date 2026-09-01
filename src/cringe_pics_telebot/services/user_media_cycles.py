import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram.types import Message

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    confirm_user_media_cycle_reservation,
    create_user_media_cycle_reservation,
    delete_expired_user_media_cycle_reservations,
    get_user_media_cycle_entries,
    lock_user_media_cycle,
    release_user_media_cycle_reservation,
    reset_user_media_cycle,
    transaction,
)
from cringe_pics_telebot.services.random_image import (
    CachedMedia,
    LinkedMedia,
    MediaChooser,
    NoCategoryMediaError,
    choose_random_image,
)

logger = logging.getLogger(__name__)

MEDIA_RESERVATION_TTL = timedelta(minutes=5)

type TokenFactory = Callable[[], str]
type MediaSender = Callable[[LinkedMedia | CachedMedia], Awaitable[Message]]


@dataclass(frozen=True, slots=True)
class UserMediaReservation:
    user_id: int
    subscription_type_id: int
    media: CategoryMedia
    token: str


@dataclass(frozen=True, slots=True)
class UserMediaCycleSelection:
    media: CategoryMedia
    starts_new_cycle: bool


def choose_user_media_cycle_image(
    media: Sequence[CategoryMedia],
    *,
    shown_media_ids: Set[int],
    reserved_media_ids: Set[int],
    last_media_id: int | None,
    chooser: MediaChooser | None = None,
) -> UserMediaCycleSelection:
    active_media = [item for item in media if item.is_active]
    if not active_media:
        raise NoCategoryMediaError

    current_media_ids = {item.id for item in active_media}
    current_shown_ids = shown_media_ids & current_media_ids
    current_reserved_ids = reserved_media_ids & current_media_ids
    remaining = [
        item for item in active_media if item.id not in current_shown_ids and item.id not in current_reserved_ids
    ]
    if remaining:
        return UserMediaCycleSelection(
            media=choose_random_image(remaining, chooser=chooser),
            starts_new_cycle=False,
        )

    if current_reserved_ids:
        raise UserMediaCycleBusyError

    new_cycle_media = active_media
    if len(active_media) > 1 and last_media_id in current_media_ids:
        new_cycle_media = [item for item in active_media if item.id != last_media_id]
    return UserMediaCycleSelection(
        media=choose_random_image(new_cycle_media, chooser=chooser),
        starts_new_cycle=True,
    )


async def reserve_user_category_media(
    *,
    user_id: int,
    subscription_type_id: int,
    media: Sequence[CategoryMedia],
    chooser: MediaChooser | None = None,
    reservation_ttl: timedelta = MEDIA_RESERVATION_TTL,
    now: datetime | None = None,
    token_factory: TokenFactory = secrets.token_urlsafe,
) -> UserMediaReservation:
    if reservation_ttl.total_seconds() <= 0:
        raise ValueError("Media reservation TTL must be positive")
    if any(item.subscription_type_id != subscription_type_id for item in media):
        raise ValueError("Media snapshot contains another subscription type")

    reserved_at = now or datetime.now(UTC)
    token = token_factory()
    if not token:
        raise ValueError("Media reservation token must not be empty")

    async with transaction():
        last_media_id = await lock_user_media_cycle(
            user_id=user_id,
            subscription_type_id=subscription_type_id,
            updated_at=reserved_at,
        )
        await delete_expired_user_media_cycle_reservations(
            user_id=user_id,
            subscription_type_id=subscription_type_id,
            expired_at=reserved_at,
        )
        entries = await get_user_media_cycle_entries(
            user_id=user_id,
            subscription_type_id=subscription_type_id,
        )
        selection = choose_user_media_cycle_image(
            media,
            shown_media_ids=entries.shown_media_ids,
            reserved_media_ids=entries.reserved_media_ids,
            last_media_id=last_media_id,
            chooser=chooser,
        )
        if selection.starts_new_cycle:
            await reset_user_media_cycle(
                user_id=user_id,
                subscription_type_id=subscription_type_id,
            )
        await create_user_media_cycle_reservation(
            user_id=user_id,
            subscription_type_id=subscription_type_id,
            media_id=selection.media.id,
            reservation_token=token,
            reserved_until=reserved_at + reservation_ttl,
        )

    return UserMediaReservation(
        user_id=user_id,
        subscription_type_id=subscription_type_id,
        media=selection.media,
        token=token,
    )


async def confirm_user_category_media(
    reservation: UserMediaReservation,
    *,
    shown_at: datetime | None = None,
) -> None:
    async with transaction():
        confirmed = await confirm_user_media_cycle_reservation(
            user_id=reservation.user_id,
            subscription_type_id=reservation.subscription_type_id,
            media_id=reservation.media.id,
            reservation_token=reservation.token,
            shown_at=shown_at or datetime.now(UTC),
        )
        if not confirmed:
            raise UserMediaReservationLostError(reservation.token)


async def release_user_category_media(reservation: UserMediaReservation) -> None:
    async with transaction():
        await release_user_media_cycle_reservation(
            user_id=reservation.user_id,
            subscription_type_id=reservation.subscription_type_id,
            media_id=reservation.media.id,
            reservation_token=reservation.token,
        )


async def deliver_user_category_media(
    *,
    user_id: int,
    subscription_type_id: int,
    media: Sequence[CategoryMedia],
    send: MediaSender,
    chooser: MediaChooser | None = None,
) -> Message:
    reservation = await reserve_user_category_media(
        user_id=user_id,
        subscription_type_id=subscription_type_id,
        media=media,
        chooser=chooser,
    )
    try:
        message = await _deliver_category_media(reservation.media, send=send)
    except BaseException:
        try:
            await asyncio.shield(release_user_category_media(reservation))
        except Exception:
            logger.exception(
                "Failed to release media reservation for user %d, subscription type %d, media %d",
                user_id,
                subscription_type_id,
                reservation.media.id,
            )
        raise

    await confirm_user_category_media(reservation)
    return message


async def _deliver_category_media(media: CategoryMedia, *, send: MediaSender) -> Message:
    from cringe_pics_telebot.services.media_delivery import deliver_category_media

    return await deliver_category_media(media, send=send)


class UserMediaCycleError(RuntimeError): ...


class UserMediaCycleBusyError(UserMediaCycleError): ...


class UserMediaReservationLostError(UserMediaCycleError): ...

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import Message
from hamcrest import assert_that, empty, equal_to, same_instance

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    get_category_media_by_subscription_types,
)
from cringe_pics_telebot.repositories.postgres import (
    connect as connect_postgres,
)
from cringe_pics_telebot.services.media_sync import MediaSyncSummary
from cringe_pics_telebot.services.user_media_cycles import (
    confirm_user_category_media,
    deliver_user_category_media,
    release_user_category_media,
    reserve_user_category_media,
)
from tests.functional.conftest import (
    POSTGRES_ENV,
    DependencyPorts,
    FakeYandexServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state(())


async def test_reservations_are_atomic_persistent_and_independent(
    docker_compose: DependencyPorts,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_functional_user: Callable[..., Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
) -> None:
    await seed_functional_subscription_types(
        (
            FunctionalSubscriptionType(1, "/cycle", None, "cycle"),
            FunctionalSubscriptionType(2, "/other", None, "other"),
        )
    )
    for user_id in (700, 701):
        await create_functional_user(user_id=user_id)
    await fake_yandex_server.configure_directory(
        "cycle",
        images=[{"name": "first.png"}, {"name": "second.png"}],
    )
    await fake_yandex_server.configure_directory("other", images=[{"name": "only.png"}])
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids(
        {
            "cycle/first.png": "telegram-first",
            "cycle/second.png": "telegram-second",
            "other/only.png": "telegram-only",
        }
    )

    async with _connect_postgres(docker_compose):
        cycle_media = await get_category_media_by_subscription_types([1])
        other_media = await get_category_media_by_subscription_types([2])
        first, second = await asyncio.gather(
            reserve_user_category_media(
                user_id=700,
                subscription_type_id=1,
                media=cycle_media,
                chooser=_choose_first,
                token_factory=lambda: "concurrent-first",
            ),
            reserve_user_category_media(
                user_id=700,
                subscription_type_id=1,
                media=cycle_media,
                chooser=_choose_first,
                token_factory=lambda: "concurrent-second",
            ),
        )
        assert_that({first.media.id, second.media.id}, equal_to({item.id for item in cycle_media}))
        await confirm_user_category_media(first)
        await release_user_category_media(second)

    async with _connect_postgres(docker_compose):
        persisted_media = await get_category_media_by_subscription_types([1])
        next_for_same_user = await reserve_user_category_media(
            user_id=700,
            subscription_type_id=1,
            media=persisted_media,
            chooser=_choose_first,
            token_factory=lambda: "same-user-next",
        )
        first_for_other_user = await reserve_user_category_media(
            user_id=701,
            subscription_type_id=1,
            media=persisted_media,
            chooser=_choose_first,
            token_factory=lambda: "other-user-first",
        )
        first_for_other_category = await reserve_user_category_media(
            user_id=700,
            subscription_type_id=2,
            media=other_media,
            chooser=_choose_first,
            token_factory=lambda: "other-category-first",
        )

        assert_that(next_for_same_user.media.id == first.media.id, equal_to(False))
        assert_that(first_for_other_user.media, same_instance(persisted_media[0]))
        assert_that(first_for_other_category.media, same_instance(other_media[0]))

        await release_user_category_media(next_for_same_user)
        await release_user_category_media(first_for_other_user)
        await release_user_category_media(first_for_other_category)


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_failed_or_cancelled_delivery_releases_reservation(
    error_type: type[BaseException],
    docker_compose: DependencyPorts,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_functional_user: Callable[..., Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
    read_functional_user_media_cycle: Callable[[int, int], Awaitable[tuple[str | None, dict[str, str]] | None]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/cycle", None, "cycle"),))
    await create_functional_user(user_id=700)
    await fake_yandex_server.configure_directory("cycle", images=[{"name": "only.png"}])
    await synchronize_functional_media_catalog()
    await set_functional_category_media_file_ids({"cycle/only.png": "telegram-only"})

    async def fail_send(_image: object) -> Message:
        raise error_type("delivery failed")

    async with _connect_postgres(docker_compose):
        media = await get_category_media_by_subscription_types([1])
        with pytest.raises(error_type):
            await deliver_user_category_media(
                user_id=700,
                subscription_type_id=1,
                media=media,
                send=fail_send,
                chooser=_choose_first,
            )

        cycle = await read_functional_user_media_cycle(700, 1)
        assert cycle is not None
        assert_that(cycle[1], empty())

        retry = await reserve_user_category_media(
            user_id=700,
            subscription_type_id=1,
            media=media,
            chooser=_choose_first,
            token_factory=lambda: "retry",
        )
        assert_that(retry.media, same_instance(media[0]))
        await release_user_category_media(retry)


async def test_expired_reservation_is_reclaimed_without_sleep(
    docker_compose: DependencyPorts,
    fake_yandex_server: FakeYandexServer,
    seed_functional_subscription_types: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    create_functional_user: Callable[..., Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    await seed_functional_subscription_types((FunctionalSubscriptionType(1, "/cycle", None, "cycle"),))
    await create_functional_user(user_id=700)
    await fake_yandex_server.configure_directory("cycle", images=[{"name": "only.png"}])
    await synchronize_functional_media_catalog()
    reserved_at = datetime(2026, 9, 1, tzinfo=UTC)

    async with _connect_postgres(docker_compose):
        media = await get_category_media_by_subscription_types([1])
        first = await reserve_user_category_media(
            user_id=700,
            subscription_type_id=1,
            media=media,
            reservation_ttl=timedelta(minutes=5),
            now=reserved_at,
            token_factory=lambda: "expired",
        )
        reclaimed = await reserve_user_category_media(
            user_id=700,
            subscription_type_id=1,
            media=media,
            reservation_ttl=timedelta(minutes=5),
            now=reserved_at + timedelta(minutes=5),
            token_factory=lambda: "reclaimed",
        )

        assert_that(reclaimed.media, same_instance(first.media))
        await release_user_category_media(reclaimed)


def _connect_postgres(dependency_ports: DependencyPorts):
    return connect_postgres(
        username=POSTGRES_ENV["POSTGRES_USER"],
        password=POSTGRES_ENV["POSTGRES_PASSWORD"],
        database=POSTGRES_ENV["POSTGRES_DB"],
        port=dependency_ports.postgres,
        host=POSTGRES_ENV["POSTGRES_HOST"],
    )


def _choose_first(items: Sequence[CategoryMedia]) -> CategoryMedia:
    return items[0]

from collections.abc import Awaitable, Callable
from datetime import time

from hamcrest import assert_that, equal_to, none

from cringe_pics_telebot.entities.annual_date import AnnualDate
from cringe_pics_telebot.entities.subscription_schedule import SubscriptionScheduleKind
from cringe_pics_telebot.entities.subscription_weekdays import SubscriptionWeekdays
from cringe_pics_telebot.repositories.postgres import (
    CreateSubscriptionType,
    connect,
    create_subscription_type,
    transaction,
    update_subscription_type_schedule,
    update_subscription_type_weekdays,
)
from tests.functional.conftest import POSTGRES_ENV, DependencyPorts, FunctionalSubscriptionType


async def test_subscription_type_repository_creates_daily_and_returns_updated_weekdays(
    docker_compose: DependencyPorts,
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_dependency_state(())

    async with connect(
        username=POSTGRES_ENV["POSTGRES_USER"],
        password=POSTGRES_ENV["POSTGRES_PASSWORD"],
        database=POSTGRES_ENV["POSTGRES_DB"],
        port=docker_compose.postgres,
        host=POSTGRES_ENV["POSTGRES_HOST"],
    ):
        async with transaction():
            created = await create_subscription_type(
                CreateSubscriptionType(
                    name="/weekdays",
                    time=time(10),
                    s3_directory_path="weekdays",
                    search_aliases=(),
                )
            )

        assert created is not None
        assert_that(created.weekdays, equal_to(SubscriptionWeekdays.daily()))
        assert_that(created.schedule_kind, equal_to(SubscriptionScheduleKind.weekly))
        assert_that(created.annual_date, none())

        selected_weekdays = SubscriptionWeekdays(1, 3, 5)
        async with transaction():
            updated = await update_subscription_type_weekdays(created.id, selected_weekdays)

        assert updated is not None
        assert_that(updated.weekdays, equal_to(selected_weekdays))
        assert_that(updated.time, equal_to(time(10)))
        assert_that(updated.search_aliases, equal_to(()))

        annual_date = AnnualDate(month=2, day=29)
        async with transaction():
            annual = await update_subscription_type_schedule(
                created.id,
                schedule_kind=SubscriptionScheduleKind.annual_date,
                annual_date=annual_date,
            )

        assert annual is not None
        assert_that(annual.schedule_kind, equal_to(SubscriptionScheduleKind.annual_date))
        assert_that(annual.annual_date, equal_to(annual_date))
        assert_that(annual.weekdays, equal_to(selected_weekdays))
        assert_that(annual.time, equal_to(time(10)))

        async with transaction():
            missing = await update_subscription_type_weekdays(999_999, selected_weekdays)

        assert_that(missing, none())

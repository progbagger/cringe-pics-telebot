from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import time
from typing import Any

import pytest
from hamcrest import assert_that, contains_string, empty, equal_to, has_entries, has_item

from cringe_pics_telebot.bot.admin_category_callback_data import (
    AdminCategoryAction,
    AdminCategoryCallbackData,
)
from tests.functional.conftest import (
    SEEDED_SUBSCRIPTION_TYPES,
    FakeTelegramServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(SEEDED_SUBSCRIPTION_TYPES)


async def test_admin_creates_inactive_category_with_all_fields(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    await fake_telegram_server.push_callback_query(data=_category_callback(AdminCategoryAction.categories))
    category_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Управление категориями" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_texts(category_list["payload"]),
        equal_to(
            [
                "✅ /day — активна",
                "✅ /evening — активна",
                "✅ /morning — активна",
                "✅ /night — активна",
                "✅ /random — активна",
                "Создать категорию",
                "Назад",
            ]
        ),
    )

    await _open_category_creation(fake_telegram_server)
    await _send_message_and_wait(fake_telegram_server, "  /afternoon  ", "путь к каталогу")
    schedule_mode = await _send_message_and_wait(
        fake_telegram_server,
        "  afternoon/images  ",
        "режим отправки",
    )
    assert_that(
        _inline_keyboard_button_texts(schedule_mode["payload"]),
        equal_to(["По расписанию", "Без расписания", "Отмена"]),
    )
    await _choose_category_schedule_mode(fake_telegram_server, scheduled=True)
    weekdays = await _send_message_and_wait(fake_telegram_server, " 15:30 ", "Новая категория — дни отправки")
    assert_that(
        _inline_keyboard_button_texts(weekdays["payload"]),
        equal_to(["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс", "Готово", "Каждый день", "Отмена"]),
    )
    for weekday in (1, 3, 5):
        await _toggle_category_weekday(fake_telegram_server, weekday)
    await _finish_category_weekdays(fake_telegram_server, daily=False)
    created = await _send_message_and_wait(
        fake_telegram_server,
        "  после обеда  \n/ДЕНЬ\nдень\n\nвечерком",
        "Категория создана неактивной",
    )

    assert_that(
        created["payload"]["text"],
        contains_string("Статус: <b>неактивна</b>"),
    )
    assert_that(created["payload"]["text"], contains_string("<code>afternoon/images</code>"))
    assert_that(created["payload"]["text"], contains_string("<code>15:30</code>"))
    assert_that(created["payload"]["text"], contains_string("Дни отправки: Пн, Ср, Пт"))
    assert_that(
        _inline_keyboard_button_texts(created["payload"]),
        equal_to(
            [
                "Активировать",
                "Изменить время отправки",
                "Изменить дни отправки",
                "Отключить расписание",
                "Изменить алиасы",
                "Очистить алиасы",
                "Назад",
            ]
        ),
    )

    category = await read_functional_subscription_type("/afternoon")
    assert category is not None
    assert_that(
        category,
        has_entries(
            name="/afternoon",
            time=time(15, 30),
            s3_directory_path="afternoon/images",
            search_aliases=("после обеда", "/ДЕНЬ", "вечерком"),
            is_active=False,
            weekdays=(1, 3, 5),
        ),
    )

    await fake_telegram_server.reset()
    await fake_telegram_server.push_callback_query(data=_category_callback(AdminCategoryAction.categories))
    updated_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "⏸ /afternoon — неактивна" in _inline_keyboard_button_texts(request["payload"]),
    )
    assert_that(
        _inline_keyboard_button_texts(updated_list["payload"]),
        has_item("⏸ /afternoon — неактивна"),
    )


async def test_admin_creates_and_activates_category_without_schedule(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    await _open_category_creation(fake_telegram_server)
    await _send_message_and_wait(fake_telegram_server, "/instant", "путь к каталогу")
    await _send_message_and_wait(fake_telegram_server, "instant", "режим отправки")
    await _choose_category_schedule_mode(fake_telegram_server, scheduled=False)
    created = await _send_message_and_wait(
        fake_telegram_server,
        "моментально",
        "Категория создана неактивной",
    )

    assert_that(created["payload"]["text"], contains_string("Локальное время отправки: без расписания"))
    assert_that(created["payload"]["text"], contains_string("Дни отправки: каждый день"))
    assert_that(
        _inline_keyboard_button_texts(created["payload"]),
        equal_to(["Активировать", "Изменить время отправки", "Изменить алиасы", "Очистить алиасы", "Назад"]),
    )
    category = await read_functional_subscription_type("/instant")
    assert category is not None
    assert_that(
        category,
        has_entries(
            time=None,
            s3_directory_path="instant",
            search_aliases=("моментально",),
            is_active=False,
            weekdays=(1, 2, 3, 4, 5, 6, 7),
        ),
    )

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.activate, category_id=category["id"]),
        message_id=102,
    )
    activated = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            "Статус: <b>активна</b>" in request["payload"].get("text", "")
            and "Локальное время отправки: без расписания" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        _inline_keyboard_button_texts(activated["payload"]),
        equal_to(["Деактивировать", "Изменить время отправки", "Изменить алиасы", "Очистить алиасы", "Назад"]),
    )
    active_category = await read_functional_subscription_type("/instant")
    assert active_category is not None
    assert_that(active_category, has_entries(time=None, is_active=True))


async def test_category_creation_validates_each_step_without_partial_write(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
    count_functional_subscription_types: Callable[[], Awaitable[int]],
) -> None:
    await set_functional_administrator(user_id=42)
    await _open_category_creation(fake_telegram_server)

    await _send_message_and_wait(fake_telegram_server, "  \n ", "Название категории не должно быть пустым")
    await _send_message_and_wait(fake_telegram_server, "/day", "уже существует")
    assert await count_functional_subscription_types() == len(SEEDED_SUBSCRIPTION_TYPES)

    await _send_message_and_wait(fake_telegram_server, "/validated", "путь к каталогу")
    await _send_message_and_wait(fake_telegram_server, "  ", "Путь к каталогу не должен быть пустым")
    assert await read_functional_subscription_type("/validated") is None

    await _send_message_and_wait(fake_telegram_server, "validated", "режим отправки")
    await _choose_category_schedule_mode(fake_telegram_server, scheduled=True)
    await _send_message_and_wait(fake_telegram_server, "9:00", "Не удалось распознать локальное время")
    await _send_message_and_wait(fake_telegram_server, "24:00", "Не удалось распознать локальное время")
    assert await read_functional_subscription_type("/validated") is None

    await _send_message_and_wait(fake_telegram_server, "09:00", "Новая категория — дни отправки")
    await fake_telegram_server.push_callback_query(data=_category_callback(AdminCategoryAction.confirm_weekdays))
    empty_weekdays = await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Выберите хотя бы один день.",
    )
    assert empty_weekdays["payload"]["show_alert"] is True
    assert await read_functional_subscription_type("/validated") is None
    await _finish_category_weekdays(fake_telegram_server, daily=True)
    await _send_message_and_wait(fake_telegram_server, " \n / \n", "Не найдено ни одного непустого алиаса")
    assert await read_functional_subscription_type("/validated") is None

    await _send_message_and_wait(fake_telegram_server, "проверка", "Категория создана неактивной")
    category = await read_functional_subscription_type("/validated")
    assert category is not None
    assert_that(
        category,
        has_entries(
            is_active=False,
            search_aliases=("проверка",),
            weekdays=(1, 2, 3, 4, 5, 6, 7),
        ),
    )


@pytest.mark.parametrize(
    "entered_values",
    [
        (),
        ("/cancelled",),
        ("/cancelled", "cancelled"),
        ("/cancelled", "cancelled", "10:00"),
        ("/cancelled", "cancelled", "10:00", "daily"),
    ],
)
async def test_category_creation_cancel_clears_every_form_state(
    entered_values: tuple[str, ...],
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    await _open_category_creation(fake_telegram_server)

    if entered_values:
        await _send_message_and_wait(fake_telegram_server, entered_values[0], "путь к каталогу")
    if len(entered_values) >= 2:
        await _send_message_and_wait(fake_telegram_server, entered_values[1], "режим отправки")
    if len(entered_values) >= 3:
        await _choose_category_schedule_mode(fake_telegram_server, scheduled=True)
        await _send_message_and_wait(fake_telegram_server, entered_values[2], "Новая категория — дни отправки")
    if len(entered_values) >= 4:
        await _finish_category_weekdays(fake_telegram_server, daily=True)

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.cancel_form),
        message_id=200,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            "Создание или редактирование категории отменено" in request["payload"].get("text", "")
        ),
    )
    assert await read_functional_subscription_type("/cancelled") is None

    await fake_telegram_server.push_message(text="остаток черновика")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Что умеет бот" in request["payload"].get("text", ""),
    )


async def test_concurrent_category_creation_handles_final_name_conflict(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
    count_functional_subscription_types: Callable[[], Awaitable[int]],
) -> None:
    await set_functional_administrator(user_id=42)
    await set_functional_administrator(user_id=43)
    await _open_category_creation(fake_telegram_server, user_id=42, message_id=100)
    await _open_category_creation(fake_telegram_server, user_id=43, message_id=101)

    for user_id in (42, 43):
        await _send_message_and_wait(fake_telegram_server, "/race", "путь к каталогу", user_id=user_id)
        await _send_message_and_wait(fake_telegram_server, "race", "режим отправки", user_id=user_id)
        await _choose_category_schedule_mode(fake_telegram_server, scheduled=True, user_id=user_id)
        await _send_message_and_wait(
            fake_telegram_server,
            "12:00",
            "Новая категория — дни отправки",
            user_id=user_id,
        )
        await _finish_category_weekdays(fake_telegram_server, daily=True, user_id=user_id)

    await _send_message_and_wait(fake_telegram_server, "гонка", "Категория создана неактивной", user_id=42)
    conflict = await _send_message_and_wait(
        fake_telegram_server,
        "гонка",
        "Категория с таким названием уже была создана",
        user_id=43,
    )

    assert_that(conflict["payload"]["text"], contains_string("Введите другое название"))
    category = await read_functional_subscription_type("/race")
    assert category is not None
    assert_that(
        category,
        has_entries(s3_directory_path="race", time=time(12, 0), is_active=False),
    )
    assert await count_functional_subscription_types() == len(SEEDED_SUBSCRIPTION_TYPES) + 1

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.cancel_form),
        user_id=43,
        message_id=202,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("chat_id") == 43 and "отменено" in request["payload"].get("text", "")
        ),
    )


async def test_admin_sets_category_activity_without_changing_category_data(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    before = await read_functional_subscription_type("/day")
    assert before is not None

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.category, category_id=2),
    )
    active_card = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Статус: <b>активна</b>" in request["payload"].get("text", ""),
    )
    assert_that(active_card["payload"]["text"], contains_string("Путь к каталогу: <code>day</code>"))
    assert_that(active_card["payload"]["text"], contains_string("Локальное время отправки: <code>13:00</code>"))
    assert_that(
        _inline_keyboard_button_texts(active_card["payload"]),
        equal_to(
            [
                "Деактивировать",
                "Изменить время отправки",
                "Изменить дни отправки",
                "Отключить расписание",
                "Изменить алиасы",
                "Очистить алиасы",
                "Назад",
            ]
        ),
    )

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.deactivate, category_id=2),
        message_id=102,
    )
    inactive_card = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Статус: <b>неактивна</b>" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Категория деактивирована.",
    )
    assert_that(
        _inline_keyboard_button_texts(inactive_card["payload"]),
        equal_to(
            [
                "Активировать",
                "Изменить время отправки",
                "Изменить дни отправки",
                "Отключить расписание",
                "Изменить алиасы",
                "Очистить алиасы",
                "Назад",
            ]
        ),
    )
    inactive = await read_functional_subscription_type("/day")
    assert inactive is not None
    assert_that(_category_business_data(inactive), equal_to(_category_business_data(before) | {"is_active": False}))

    await fake_telegram_server.reset()
    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.deactivate, category_id=2),
        message_id=103,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Статус: <b>неактивна</b>" in request["payload"].get("text", ""),
    )
    assert_that(
        _category_business_data(await _required_category(read_functional_subscription_type, "/day")),
        equal_to(_category_business_data(before) | {"is_active": False}),
    )

    await fake_telegram_server.reset()
    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.activate, category_id=2),
        message_id=104,
    )
    reactivated_card = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Статус: <b>активна</b>" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Категория активирована.",
    )
    assert_that(
        _inline_keyboard_button_texts(reactivated_card["payload"]),
        equal_to(
            [
                "Деактивировать",
                "Изменить время отправки",
                "Изменить дни отправки",
                "Отключить расписание",
                "Изменить алиасы",
                "Очистить алиасы",
                "Назад",
            ]
        ),
    )
    reactivated = await _required_category(read_functional_subscription_type, "/day")
    assert_that(_category_business_data(reactivated), equal_to(_category_business_data(before)))


async def test_admin_updates_disables_and_restores_category_schedule(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
    create_user_subscription: Callable[..., Awaitable[None]],
    count_user_subscriptions: Callable[[int], Awaitable[int]],
) -> None:
    await set_functional_administrator(user_id=42)
    await create_user_subscription(user_id=700, subscription_type_id=2)
    before_schedule = await _required_category(read_functional_subscription_type, "/day")

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.edit_weekdays, category_id=2),
    )
    edit_weekdays = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Дни отправки категории /day" in request["payload"].get("text", ""),
    )
    assert_that(
        _inline_keyboard_button_texts(edit_weekdays["payload"]),
        equal_to(
            [
                "✅ Пн",
                "✅ Вт",
                "✅ Ср",
                "✅ Чт",
                "✅ Пт",
                "✅ Сб",
                "✅ Вс",
                "Готово",
                "Каждый день",
                "Отмена",
            ]
        ),
    )
    for weekday in (2, 4, 6, 7):
        await _toggle_category_weekday(fake_telegram_server, weekday)
    await _finish_category_weekdays(
        fake_telegram_server,
        daily=False,
        expected_text="Дни отправки: Пн, Ср, Пт",
    )
    selected_schedule = await _required_category(read_functional_subscription_type, "/day")
    assert_that(
        _category_business_data(selected_schedule),
        equal_to(_category_business_data(before_schedule) | {"weekdays": (1, 3, 5)}),
    )

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.edit_time, category_id=2),
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Изменение времени отправки" in request["payload"].get("text", ""),
    )
    await _send_message_and_wait(fake_telegram_server, "9:00", "Не удалось распознать локальное время")
    unchanged = await read_functional_subscription_type("/day")
    assert unchanged is not None
    assert_that(unchanged["time"], equal_to(time(13)))

    updated = await _send_message_and_wait(fake_telegram_server, "14:30", "Время отправки обновлено")
    assert_that(updated["payload"]["text"], contains_string("<code>14:30</code>"))
    changed = await read_functional_subscription_type("/day")
    assert changed is not None
    assert_that(changed["time"], equal_to(time(14, 30)))

    await fake_telegram_server.reset()
    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.disable_schedule, category_id=2),
        message_id=102,
    )
    disabled = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Локальное время отправки: без расписания" in request["payload"].get("text", ""),
    )
    await fake_telegram_server.wait_for_request(
        "answerCallbackQuery",
        predicate=lambda request: request["payload"].get("text") == "Расписание отключено.",
    )
    assert_that(
        _inline_keyboard_button_texts(disabled["payload"]),
        equal_to(["Деактивировать", "Изменить время отправки", "Изменить алиасы", "Очистить алиасы", "Назад"]),
    )
    without_schedule = await read_functional_subscription_type("/day")
    assert without_schedule is not None
    assert without_schedule["time"] is None
    assert_that(without_schedule["weekdays"], equal_to((1, 3, 5)))
    assert_that(await count_user_subscriptions(700), equal_to(1))

    await fake_telegram_server.reset()
    await fake_telegram_server.push_message(text="/subscriptions", user_id=700)
    hidden_subscriptions = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: (
            request["payload"].get("chat_id") == 700 and "список" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        [text for text in _inline_keyboard_button_texts(hidden_subscriptions["payload"]) if "/day" in text],
        empty(),
    )

    await fake_telegram_server.reset()
    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.edit_time, category_id=2),
        message_id=103,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Изменение времени отправки" in request["payload"].get("text", ""),
    )
    restored = await _send_message_and_wait(fake_telegram_server, "16:00", "Время отправки обновлено")
    assert_that(restored["payload"]["text"], contains_string("<code>16:00</code>"))

    await fake_telegram_server.reset()
    await fake_telegram_server.push_message(text="/subscriptions", user_id=700)
    restored_subscriptions = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: (
            request["payload"].get("chat_id") == 700 and "список" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        _inline_keyboard_button_texts(restored_subscriptions["payload"]),
        has_item("✅ /day – 16:00 · Пн, Ср, Пт"),
    )
    assert_that(await count_user_subscriptions(700), equal_to(1))


async def test_admin_cancels_weekday_edit_without_changing_category(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    read_functional_subscription_type: Callable[[str], Awaitable[dict[str, Any] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    before = await _required_category(read_functional_subscription_type, "/day")

    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.edit_weekdays, category_id=2),
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Дни отправки категории /day" in request["payload"].get("text", ""),
    )
    await _toggle_category_weekday(fake_telegram_server, 1)
    assert_that(
        _category_business_data(await _required_category(read_functional_subscription_type, "/day")),
        equal_to(_category_business_data(before)),
    )

    await fake_telegram_server.push_callback_query(data=_category_callback(AdminCategoryAction.cancel_form))
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            "Создание или редактирование категории отменено" in request["payload"].get("text", "")
        ),
    )
    assert_that(
        _category_business_data(await _required_category(read_functional_subscription_type, "/day")),
        equal_to(_category_business_data(before)),
    )
    assert_that(len(await fake_telegram_server.requests(method="answerCallbackQuery")), equal_to(3))


async def _open_category_creation(
    fake_telegram_server: FakeTelegramServer,
    *,
    user_id: int = 42,
    message_id: int = 100,
) -> None:
    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.create),
        user_id=user_id,
        message_id=message_id,
    )
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("chat_id") == user_id
            and "Новая категория — название" in request["payload"].get("text", "")
        ),
    )


async def _choose_category_schedule_mode(
    fake_telegram_server: FakeTelegramServer,
    *,
    scheduled: bool,
    user_id: int = 42,
) -> None:
    await fake_telegram_server.push_callback_query(
        data=_category_callback(
            AdminCategoryAction.create_scheduled if scheduled else AdminCategoryAction.create_without_schedule
        ),
        user_id=user_id,
    )
    expected_prompt = "Новая категория — время отправки" if scheduled else "Новая категория — алиасы"
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("chat_id") == user_id and expected_prompt in request["payload"].get("text", "")
        ),
    )


async def _send_message_and_wait(
    fake_telegram_server: FakeTelegramServer,
    text: str,
    response_text: str,
    *,
    user_id: int = 42,
) -> dict[str, Any]:
    await fake_telegram_server.push_message(text=text, user_id=user_id)
    return await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: (
            request["payload"].get("chat_id") == user_id and response_text in request["payload"].get("text", "")
        ),
    )


async def _toggle_category_weekday(
    fake_telegram_server: FakeTelegramServer,
    weekday: int,
    *,
    user_id: int = 42,
) -> dict[str, Any]:
    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.toggle_weekday, weekday=weekday),
        user_id=user_id,
    )
    return await fake_telegram_server.wait_for_request(
        "editMessageReplyMarkup",
        predicate=lambda request: request["payload"].get("chat_id") == user_id,
    )


async def _finish_category_weekdays(
    fake_telegram_server: FakeTelegramServer,
    *,
    daily: bool,
    user_id: int = 42,
    expected_text: str = "Новая категория — алиасы",
) -> dict[str, Any]:
    await fake_telegram_server.push_callback_query(
        data=_category_callback(AdminCategoryAction.daily_weekdays if daily else AdminCategoryAction.confirm_weekdays),
        user_id=user_id,
    )
    return await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            request["payload"].get("chat_id") == user_id and expected_text in request["payload"].get("text", "")
        ),
    )


def _category_callback(action: AdminCategoryAction, category_id: int = 0, weekday: int = 0) -> str:
    return AdminCategoryCallbackData(action=action, category_id=category_id, weekday=weekday).pack()


def _inline_keyboard_button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]


def _category_business_data(category: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": category["name"],
        "time": category["time"],
        "s3_directory_path": category["s3_directory_path"],
        "search_aliases": category["search_aliases"],
        "is_active": category["is_active"],
        "weekdays": category["weekdays"],
    }


async def _required_category(
    reader: Callable[[str], Awaitable[dict[str, Any] | None]],
    name: str,
) -> dict[str, Any]:
    category = await reader(name)
    assert category is not None
    return category

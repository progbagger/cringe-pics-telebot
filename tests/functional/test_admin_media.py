from asyncio import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from hamcrest import assert_that, contains_string, equal_to

from cringe_pics_telebot.bot.admin_category_callback_data import AdminCategoryAction, AdminCategoryPagedCallbackData
from cringe_pics_telebot.bot.admin_media_callback_data import AdminMediaAction, AdminMediaCallbackData
from cringe_pics_telebot.services.media_sync import MediaSyncSummary
from tests.functional.conftest import (
    SEEDED_SUBSCRIPTION_TYPES,
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(SEEDED_SUBSCRIPTION_TYPES)


async def test_admin_pages_media_and_replaces_then_clears_search_aliases(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_media_search_aliases: Callable[[dict[str, tuple[str, ...]]], Awaitable[None]],
    read_functional_media_search_aliases: Callable[[str], Awaitable[tuple[str, ...] | None]],
) -> None:
    image_names = [f"{index:02}.png" for index in range(9)]
    await set_functional_administrator(user_id=42)
    await fake_yandex_server.configure_directory("day", images=[{"name": name} for name in image_names])
    await synchronize_functional_media_catalog()
    await set_functional_media_search_aliases({"day/08.png": ("Кот",)})

    await fake_telegram_server.push_callback_query(
        data=AdminCategoryPagedCallbackData(
            action=AdminCategoryAction.category,
            category_id=2,
            page=3,
        ).pack()
    )
    category_card = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<b>Категория /day</b>" in request["payload"].get("text", ""),
    )
    media_list_callback = _button_callback_data(category_card["payload"], "Медиа и алиасы")
    assert media_list_callback is not None
    unpacked_list = AdminMediaCallbackData.unpack(media_list_callback)
    assert_that(
        (unpacked_list.category_id, unpacked_list.category_page, unpacked_list.media_page),
        equal_to((2, 3, 0)),
    )

    await fake_telegram_server.push_callback_query(data=media_list_callback, message_id=101)
    first_page = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<b>Медиа категории /day</b>" in request["payload"].get("text", ""),
    )
    assert_that(
        _button_texts(first_page["payload"]),
        equal_to([*image_names[:8], "Назад", ">"]),
    )

    next_callback = _button_callback_data(first_page["payload"], ">")
    assert next_callback is not None
    await fake_telegram_server.push_callback_query(data=next_callback, message_id=102)
    second_page = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: request["payload"].get("message_id") == 102,
    )
    assert_that(_button_texts(second_page["payload"]), equal_to(["08.png", "Назад", "<"]))

    media_callback = _button_callback_data(second_page["payload"], "08.png")
    assert media_callback is not None
    await fake_telegram_server.push_callback_query(data=media_callback, message_id=103)
    media_card = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<b>Медиа 08.png</b>" in request["payload"].get("text", ""),
    )
    assert_that(media_card["payload"]["text"], contains_string("Путь: <code>day/08.png</code>"))
    assert_that(media_card["payload"]["text"], contains_string("• <code>Кот</code>"))

    edit_callback = _button_callback_data(media_card["payload"], "Изменить алиасы медиа")
    assert edit_callback is not None
    await fake_telegram_server.push_callback_query(data=edit_callback, message_id=104)
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<b>Алиасы медиа 08.png</b>" in request["payload"].get("text", ""),
    )
    preview = await fake_telegram_server.wait_for_request("sendPhoto")
    assert_that(preview["payload"]["photo"], contains_string("/download/08.png"))

    await fake_telegram_server.push_message(text=" \n ")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Не найдено ни одного" in request["payload"].get("text", ""),
    )
    assert_that(await read_functional_media_search_aliases("day/08.png"), equal_to(("Кот",)))

    await fake_telegram_server.push_message(text="  Сонный кот  \nсонный   кот\nСПИТ")
    updated_card = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Алиасы медиа обновлены" in request["payload"].get("text", ""),
    )
    assert_that(
        await read_functional_media_search_aliases("day/08.png"),
        equal_to(("Сонный кот", "СПИТ")),
    )
    back_callback = _button_callback_data(updated_card["payload"], "Назад")
    assert back_callback is not None
    unpacked_back = AdminMediaCallbackData.unpack(back_callback)
    assert_that((unpacked_back.category_page, unpacked_back.media_page), equal_to((3, 1)))

    clear_callback = _button_callback_data(updated_card["payload"], "Очистить алиасы медиа")
    assert clear_callback is not None
    await fake_telegram_server.push_callback_query(data=clear_callback, message_id=105)
    cleared_card = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: request["payload"].get("message_id") == 105,
    )
    assert_that(cleared_card["payload"]["text"], contains_string("<i>не заданы</i>"))
    assert_that(await read_functional_media_search_aliases("day/08.png"), equal_to(()))
    assert_that(
        _button_texts(cleared_card["payload"]),
        equal_to(["Изменить алиасы медиа", "Назад"]),
    )


async def test_admin_media_form_handles_media_deactivated_during_edit(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    set_functional_media_search_aliases: Callable[[dict[str, tuple[str, ...]]], Awaitable[None]],
    read_functional_media_search_aliases: Callable[[str], Awaitable[tuple[str, ...] | None]],
) -> None:
    await set_functional_administrator(user_id=42)
    await fake_yandex_server.configure_directory("day", images=[{"name": "gone.png"}])
    await synchronize_functional_media_catalog()
    await set_functional_media_search_aliases({"day/gone.png": ("старый",)})

    media_list_callback = AdminMediaCallbackData(action=AdminMediaAction.media_list, category_id=2).pack()
    await fake_telegram_server.push_callback_query(data=media_list_callback, message_id=201)
    media_list = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Медиа категории /day" in request["payload"].get("text", ""),
    )
    media_callback = _button_callback_data(media_list["payload"], "gone.png")
    assert media_callback is not None
    await fake_telegram_server.push_callback_query(data=media_callback, message_id=202)
    media_card = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "<b>Медиа gone.png</b>" in request["payload"].get("text", ""),
    )
    edit_callback = _button_callback_data(media_card["payload"], "Изменить алиасы медиа")
    assert edit_callback is not None
    await fake_telegram_server.push_callback_query(data=edit_callback, message_id=203)
    await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Алиасы медиа gone.png" in request["payload"].get("text", ""),
    )

    await fake_yandex_server.configure_directory("day", images=[])
    await synchronize_functional_media_catalog()
    await fake_telegram_server.push_message(text="новый")
    unavailable = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Медиа больше недоступно" in request["payload"].get("text", ""),
    )

    assert_that(await read_functional_media_search_aliases("day/gone.png"), equal_to(("старый",)))
    assert_that(_button_texts(unavailable["payload"]), equal_to(["Назад"]))


def _button_texts(payload: dict[str, Any]) -> list[str]:
    return [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]


def _button_callback_data(payload: dict[str, Any], text: str) -> str | None:
    for row in payload["reply_markup"]["inline_keyboard"]:
        for button in row:
            if button["text"] == text:
                return button.get("callback_data")
    return None

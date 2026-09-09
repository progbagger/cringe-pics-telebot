from datetime import UTC, datetime, time
from typing import Any, cast

import pytest
from aiogram.fsm.context import FSMContext
from hamcrest import assert_that, equal_to, none, same_instance
from pytest import MonkeyPatch

from cringe_pics_telebot.bot import admin_media as admin_media_bot
from cringe_pics_telebot.bot.admin_keyboards import (
    create_admin_media_keyboard,
    create_admin_media_list_keyboard,
)
from cringe_pics_telebot.bot.admin_media_callback_data import AdminMediaAction, AdminMediaCallbackData
from cringe_pics_telebot.entities.search_aliases import SearchAlias
from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaSearchMetadata,
    CategoryMediaStatus,
    SubscriptionType,
    TelegramMediaType,
)
from cringe_pics_telebot.services import admin_media
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia


async def test_get_admin_media_catalog_combines_category_and_active_media(monkeypatch: MonkeyPatch) -> None:
    category = _category()
    metadata = (_metadata(1), _metadata(2))

    monkeypatch.setattr(admin_media, "get_subscription_type", lambda category_id: _async_result(category))

    async def get_media(category_id: int, *, active_only: bool) -> list[CategoryMediaSearchMetadata]:
        assert_that(category_id, equal_to(category.id))
        assert_that(active_only, equal_to(True))
        return list(metadata)

    monkeypatch.setattr(admin_media, "get_category_media_search_metadata_by_subscription_type", get_media)

    catalog = await admin_media.get_admin_media_catalog(category.id)

    assert catalog is not None
    assert_that(catalog.category, same_instance(category))
    assert_that(catalog.media, equal_to(metadata))


@pytest.mark.parametrize(
    ("category_id", "media_category_id", "is_active", "available"),
    [(2, 2, True, True), (2, 3, True, False), (2, 2, False, False)],
)
async def test_get_admin_media_validates_category_and_activity(
    monkeypatch: MonkeyPatch,
    category_id: int,
    media_category_id: int,
    is_active: bool,
    available: bool,
) -> None:
    metadata = _metadata(1, category_id=media_category_id, is_active=is_active)
    monkeypatch.setattr(
        admin_media,
        "get_category_media_search_metadata",
        lambda media_id: _async_result(metadata),
    )

    result = await admin_media.get_admin_media(category_id, metadata.media.id)

    if available:
        assert result is metadata
    else:
        assert_that(result, none())


async def test_update_admin_media_aliases_scopes_locked_update(monkeypatch: MonkeyPatch) -> None:
    aliases = (SearchAlias(text="Кот", normalized="кот"),)
    metadata = _metadata(1, aliases=("Кот",))

    async def set_aliases(
        media_id: int,
        received_aliases: tuple[SearchAlias, ...],
        *,
        subscription_type_id: int,
        active_only: bool,
    ) -> CategoryMediaSearchMetadata:
        assert_that(media_id, equal_to(1))
        assert received_aliases is aliases
        assert_that(subscription_type_id, equal_to(2))
        assert_that(active_only, equal_to(True))
        return metadata

    monkeypatch.setattr(admin_media, "set_media_search_aliases", set_aliases)

    assert await admin_media.update_admin_media_search_aliases(2, 1, aliases) is metadata


async def test_admin_media_preview_uses_persisted_telegram_file_id(monkeypatch: MonkeyPatch) -> None:
    metadata = _metadata(1, file_id="telegram-preview")

    async def fail_get_urls(paths: tuple[str, ...]) -> list[str | None]:
        raise AssertionError("Yandex URL must not be requested for cached media")

    monkeypatch.setattr(admin_media, "get_download_urls", fail_get_urls)

    preview = await admin_media.get_admin_media_preview(metadata)

    assert_that(
        preview,
        equal_to(
            CachedMedia(
                name="1.png",
                mime_type="image/png",
                path="day/1.png",
                source_revision="sha256:1",
                id="telegram-preview",
            )
        ),
    )


@pytest.mark.parametrize(
    ("download_url", "expected"),
    [
        (
            "https://storage.example/day/1.png",
            LinkedMedia(
                name="1.png",
                mime_type="image/png",
                path="day/1.png",
                source_revision="sha256:1",
                url="https://storage.example/day/1.png",
            ),
        ),
        (None, None),
    ],
)
async def test_admin_media_preview_resolves_pending_media_url(
    monkeypatch: MonkeyPatch,
    download_url: str | None,
    expected: LinkedMedia | None,
) -> None:
    metadata = _metadata(1)

    async def get_urls(paths: tuple[str, ...]) -> list[str | None]:
        assert_that(paths, equal_to(("day/1.png",)))
        return [download_url]

    monkeypatch.setattr(admin_media, "get_download_urls", get_urls)

    assert_that(await admin_media.get_admin_media_preview(metadata), equal_to(expected))


def test_admin_media_list_keyboard_preserves_both_pages_and_normalizes_requested_page() -> None:
    metadata = tuple(_metadata(index) for index in range(1, 10))

    markup = create_admin_media_list_keyboard(
        metadata,
        category_id=2,
        category_page=3,
        media_page=99,
    )
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert_that([button.text for button in buttons], equal_to(["9.png", "Назад", "<"]))
    media_callback = AdminMediaCallbackData.unpack(buttons[0].callback_data or "")
    assert_that(
        (
            media_callback.action,
            media_callback.category_id,
            media_callback.media_id,
            media_callback.category_page,
            media_callback.media_page,
        ),
        equal_to((AdminMediaAction.media, 2, 9, 3, 1)),
    )


def test_admin_media_keyboard_keeps_callbacks_short_and_clear_action_conditional() -> None:
    without_aliases = create_admin_media_keyboard(
        category_id=9_223_372_036_854_775_807,
        media_id=9_223_372_036_854_775_807,
        has_aliases=False,
        category_page=999,
        media_page=999,
    )
    with_aliases = create_admin_media_keyboard(
        category_id=2,
        media_id=7,
        has_aliases=True,
    )

    assert_that(
        [button.text for row in without_aliases.inline_keyboard for button in row],
        equal_to(["Изменить алиасы медиа", "Назад"]),
    )
    assert_that(
        [button.text for row in with_aliases.inline_keyboard for button in row],
        equal_to(["Изменить алиасы медиа", "Очистить алиасы медиа", "Назад"]),
    )
    assert all(
        button.callback_data is not None and len(button.callback_data.encode()) <= 64
        for row in without_aliases.inline_keyboard
        for button in row
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"category_id": 2},
        {"category_id": 2, "media_id": "7"},
        {"category_id": 2, "media_id": 7, "category_page": None},
    ],
)
async def test_admin_media_location_rejects_lost_or_invalid_draft(data: dict[str, Any]) -> None:
    state = _FakeState(data)

    assert_that(await admin_media_bot._state_location(cast(FSMContext, state)), none())
    assert_that(state.cleared, equal_to(True))


async def _async_result[T](value: T) -> T:
    return value


class _FakeState:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.cleared = False

    async def get_data(self) -> dict[str, Any]:
        return self.data

    async def clear(self) -> None:
        self.cleared = True


def _category() -> SubscriptionType:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    return SubscriptionType(
        id=2,
        name="/day",
        time=time(13),
        s3_directory_path="day",
        search_aliases=("день",),
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _metadata(
    media_id: int,
    *,
    category_id: int = 2,
    is_active: bool = True,
    aliases: tuple[str, ...] = (),
    file_id: str | None = None,
) -> CategoryMediaSearchMetadata:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    media = CategoryMedia(
        id=media_id,
        subscription_type_id=category_id,
        source_path=f"day/{media_id}.png",
        source_revision=f"sha256:{media_id}",
        name=f"{media_id}.png",
        mime_type="image/png",
        telegram_media_type=TelegramMediaType.photo,
        telegram_file_id=file_id,
        telegram_file_unique_id="unique" if file_id is not None else None,
        is_active=is_active,
        status=(
            CategoryMediaStatus.inactive
            if not is_active
            else CategoryMediaStatus.ready
            if file_id is not None
            else CategoryMediaStatus.pending
        ),
        last_seen_at=now,
        materialized_at=now if file_id is not None else None,
        created_at=now,
        updated_at=now,
    )
    return CategoryMediaSearchMetadata(media=media, search_aliases=aliases)

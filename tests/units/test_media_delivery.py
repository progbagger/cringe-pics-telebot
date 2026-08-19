import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import Message

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaStatus,
    TelegramMediaType,
)
from cringe_pics_telebot.services import media_delivery
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia


@pytest.fixture(autouse=True)
def no_database_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def transaction():
        yield

    monkeypatch.setattr(media_delivery, "transaction", transaction)


async def test_ready_media_is_sent_without_redis_or_yandex(monkeypatch: pytest.MonkeyPatch) -> None:
    send = AsyncMock(return_value=_message())
    acquire = AsyncMock()
    download = AsyncMock()
    monkeypatch.setattr(media_delivery.cache, "set_if_absent", acquire)
    monkeypatch.setattr(media_delivery, "get_download_urls", download)

    result = await media_delivery.deliver_category_media(_media(file_id="ready-file-id"), send=send)

    assert result is send.return_value
    await_args = send.await_args
    assert await_args is not None
    sent_media = await_args.args[0]
    assert isinstance(sent_media, CachedMedia)
    assert sent_media.id == "ready-file-id"
    acquire.assert_not_awaited()
    download.assert_not_awaited()


async def test_pending_media_is_materialized_after_real_send(monkeypatch: pytest.MonkeyPatch) -> None:
    media = _media()
    send = AsyncMock(return_value=_message())
    monkeypatch.setattr(media_delivery.cache, "set_if_absent", AsyncMock(return_value=True))
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(media_delivery.cache, "delete_if_value", release)
    monkeypatch.setattr(media_delivery, "get_download_urls", AsyncMock(return_value=["https://media.test/image.png"]))
    monkeypatch.setattr(
        media_delivery,
        "get_message_media_file_ids",
        lambda message: ("telegram-file-id", "telegram-unique-id"),
    )
    materialize = AsyncMock(return_value=_media(file_id="telegram-file-id"))
    monkeypatch.setattr(media_delivery, "materialize_category_media", materialize)

    await media_delivery.deliver_category_media(media, send=send)

    await_args = send.await_args
    assert await_args is not None
    sent_media = await_args.args[0]
    assert isinstance(sent_media, LinkedMedia)
    assert sent_media.url == "https://media.test/image.png"
    materialize.assert_awaited_once_with(
        media_id=media.id,
        source_revision=media.source_revision,
        telegram_file_id="telegram-file-id",
        telegram_file_unique_id="telegram-unique-id",
    )
    release.assert_awaited_once()


async def test_concurrent_delivery_uploads_pending_revision_once(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _media()
    ready = _media(file_id="telegram-file-id")
    owner_started = asyncio.Event()
    allow_owner_to_finish = asyncio.Event()
    materialized = asyncio.Event()
    acquire_calls = 0

    async def acquire(**kwargs) -> bool:
        nonlocal acquire_calls
        acquire_calls += 1
        return acquire_calls == 1

    async def send(image: LinkedMedia | CachedMedia) -> Message:
        if isinstance(image, LinkedMedia):
            owner_started.set()
            await allow_owner_to_finish.wait()
        return _message()

    async def materialize(**kwargs) -> CategoryMedia:
        materialized.set()
        return ready

    async def wait_for_materialization(seconds: float) -> None:
        await materialized.wait()

    monkeypatch.setattr(media_delivery.cache, "set_if_absent", acquire)
    monkeypatch.setattr(media_delivery.cache, "delete_if_value", AsyncMock(return_value=True))
    download = AsyncMock(return_value=["https://media.test/image.png"])
    monkeypatch.setattr(media_delivery, "get_download_urls", download)
    monkeypatch.setattr(media_delivery, "get_message_media_file_ids", lambda message: ("telegram-file-id", "unique"))
    monkeypatch.setattr(media_delivery, "materialize_category_media", materialize)
    monkeypatch.setattr(media_delivery, "get_category_media", AsyncMock(return_value=ready))

    owner = asyncio.create_task(media_delivery.deliver_category_media(pending, send=send))
    await owner_started.wait()
    waiter = asyncio.create_task(
        media_delivery.deliver_category_media(
            pending,
            send=send,
            sleep=wait_for_materialization,
        )
    )
    allow_owner_to_finish.set()
    await asyncio.gather(owner, waiter)

    download.assert_awaited_once()
    assert acquire_calls == 2


async def test_invalid_file_id_is_cleared_and_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = _media(file_id="invalid-file-id")
    pending = _media()
    send = AsyncMock(
        side_effect=[
            TelegramBadRequest(
                method=SendPhoto(chat_id=1, photo="invalid-file-id"),
                message="Bad Request: wrong file identifier/HTTP URL specified",
            ),
            _message(),
        ]
    )
    invalidate = AsyncMock(return_value=pending)
    monkeypatch.setattr(media_delivery, "invalidate_category_media_file_id", invalidate)
    monkeypatch.setattr(media_delivery.cache, "set_if_absent", AsyncMock(return_value=True))
    monkeypatch.setattr(media_delivery.cache, "delete_if_value", AsyncMock(return_value=True))
    monkeypatch.setattr(media_delivery, "get_download_urls", AsyncMock(return_value=["https://media.test/image.png"]))
    monkeypatch.setattr(media_delivery, "get_message_media_file_ids", lambda message: ("new-file-id", "unique"))
    monkeypatch.setattr(media_delivery, "materialize_category_media", AsyncMock(return_value=None))

    await media_delivery.deliver_category_media(ready, send=send)

    invalidate.assert_awaited_once_with(media_id=ready.id, telegram_file_id="invalid-file-id")
    assert isinstance(send.await_args_list[0].args[0], CachedMedia)
    assert isinstance(send.await_args_list[1].args[0], LinkedMedia)


async def test_cancellation_releases_owner_lease_without_materializing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancel_send(image: LinkedMedia | CachedMedia) -> Message:
        raise asyncio.CancelledError

    monkeypatch.setattr(media_delivery.cache, "set_if_absent", AsyncMock(return_value=True))
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(media_delivery.cache, "delete_if_value", release)
    monkeypatch.setattr(media_delivery, "get_download_urls", AsyncMock(return_value=["https://media.test/image.png"]))
    materialize = AsyncMock()
    monkeypatch.setattr(media_delivery, "materialize_category_media", materialize)

    with pytest.raises(asyncio.CancelledError):
        await media_delivery.deliver_category_media(_media(), send=cancel_send)

    release.assert_awaited_once()
    materialize.assert_not_awaited()


def _media(*, file_id: str | None = None) -> CategoryMedia:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return CategoryMedia(
        id=7,
        subscription_type_id=1,
        source_path="day/image.png",
        source_revision="sha256:revision",
        name="image.png",
        mime_type="image/png",
        telegram_media_type=TelegramMediaType.photo,
        telegram_file_id=file_id,
        telegram_file_unique_id="unique" if file_id is not None else None,
        is_active=True,
        status=CategoryMediaStatus.ready if file_id is not None else CategoryMediaStatus.pending,
        last_seen_at=now,
        materialized_at=now if file_id is not None else None,
        created_at=now,
        updated_at=now,
    )


def _message() -> Message:
    return cast(Message, object())

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from cringe_pics_telebot.bot import admin_panel
from cringe_pics_telebot.bot.admin_panel import _media_sync_summary_text
from cringe_pics_telebot.services.media_sync import MediaSyncSummary


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (
            MediaSyncSummary(
                acquired=True,
                categories=2,
                discovered=3,
                created=2,
                changed=1,
                reactivated=1,
                deactivated=1,
            ),
            "<b>Синхронизация медиа завершена</b>\n\n"
            "Обработано категорий: <b>2</b>\n"
            "Категорий с ошибками: <b>0</b>\n"
            "Найдено медиа: <b>3</b>\n"
            "Создано записей: <b>2</b>\n"
            "Изменено записей: <b>1</b>\n"
            "Повторно активировано медиа: <b>1</b>\n"
            "Деактивировано отсутствующее медиа: <b>1</b>",
        ),
        (
            MediaSyncSummary(acquired=True, categories=1, failed=1, discovered=2, created=1),
            "<b>Синхронизация медиа завершена частично</b>\n\n"
            "Обработано категорий: <b>1</b>\n"
            "Категорий с ошибками: <b>1</b>\n"
            "Найдено медиа: <b>2</b>\n"
            "Создано записей: <b>1</b>\n"
            "Изменено записей: <b>0</b>\n"
            "Повторно активировано медиа: <b>0</b>\n"
            "Деактивировано отсутствующее медиа: <b>0</b>",
        ),
        (
            MediaSyncSummary(acquired=False),
            "<b>Синхронизация медиа уже выполняется</b>\n\nНовый запуск не начат.",
        ),
    ],
    ids=("success", "partial", "occupied"),
)
def test_media_sync_summary_text(summary: MediaSyncSummary, expected: str) -> None:
    assert _media_sync_summary_text(summary) == expected


async def test_manual_media_sync_logs_unexpected_error_and_shows_safe_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = AsyncMock()
    synchronize = AsyncMock(side_effect=RuntimeError("sensitive details"))
    monkeypatch.setattr(admin_panel, "synchronize_media_catalog", synchronize)

    with caplog.at_level(logging.ERROR, logger=admin_panel.__name__):
        await admin_panel._synchronize_media(message)

    assert message.edit_text.await_count == 2
    assert message.edit_text.await_args_list[1].args[0] == (
        "<b>Не удалось синхронизировать медиа</b>\n\nПопробуйте повторить позже."
    )
    assert "sensitive details" not in message.edit_text.await_args_list[1].args[0]
    assert any(record.exc_info is not None for record in caplog.records)


async def test_manual_media_sync_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    message = AsyncMock()
    synchronize = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(admin_panel, "synchronize_media_catalog", synchronize)

    with pytest.raises(asyncio.CancelledError):
        await admin_panel._synchronize_media(message)

    assert message.edit_text.await_count == 1

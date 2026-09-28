from unittest.mock import AsyncMock

import pytest

from cringe_pics_telebot.services import data_sync
from cringe_pics_telebot.services.birthday_sync import BirthdaySyncSummary
from cringe_pics_telebot.services.media_sync import MediaSyncSummary


async def test_profile_stage_runs_after_media_stage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    profiles = BirthdaySyncSummary(acquired=True, completed=True, considered=1, unchanged=1)
    monkeypatch.setattr(data_sync, "synchronize_media_catalog", AsyncMock(side_effect=RuntimeError))
    monkeypatch.setattr(data_sync, "synchronize_telegram_birthdays", AsyncMock(return_value=profiles))

    result = await data_sync.synchronize_bot_data(bot)

    assert result.media is None
    assert result.profiles is profiles


async def test_media_result_is_kept_after_profile_stage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    media = MediaSyncSummary(acquired=True, categories=1)
    monkeypatch.setattr(data_sync, "synchronize_media_catalog", AsyncMock(return_value=media))
    monkeypatch.setattr(data_sync, "synchronize_telegram_birthdays", AsyncMock(side_effect=RuntimeError))

    result = await data_sync.synchronize_bot_data(bot)

    assert result.media is media
    assert result.profiles is None

import asyncio
from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock

import pytest
from hamcrest import assert_that, equal_to, has_length, has_properties, is_, same_instance

from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaReconcileResult,
    SubscriptionType,
    TelegramMediaType,
)
from cringe_pics_telebot.repositories.yandex import Image
from cringe_pics_telebot.services import media_sync


async def test_runner_synchronizes_immediately_then_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    synchronize = AsyncMock()
    monkeypatch.setattr(media_sync, "synchronize_media_catalog", synchronize)

    async def cancel_during_sleep(seconds: float) -> None:
        assert_that(seconds, equal_to(43_200))
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await media_sync.run_media_sync(sleep=cancel_during_sleep)

    synchronize.assert_awaited_once_with()


async def test_synchronization_isolates_category_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    categories = [_subscription_type(1, "/day", "day"), _subscription_type(2, "/broken", "broken")]
    monkeypatch.setattr(media_sync, "get_all_subscription_types", AsyncMock(return_value=categories))
    monkeypatch.setattr(media_sync.cache, "set_if_absent", AsyncMock(return_value=True))
    monkeypatch.setattr(media_sync.cache, "refresh_if_value", AsyncMock(return_value=True))
    delete_lease = AsyncMock(return_value=True)
    monkeypatch.setattr(media_sync.cache, "delete_if_value", delete_lease)

    async def list_images(directory: str):
        if directory == "broken":
            raise RuntimeError("listing failed")
        yield _image("day/image.gif", mime_type="image/gif")

    monkeypatch.setattr(media_sync, "list_dir", list_images)
    reconcile = AsyncMock(
        return_value=CategoryMediaReconcileResult(
            discovered=1,
            created=1,
            changed=0,
            reactivated=0,
            deactivated=0,
            unchanged=0,
        )
    )
    monkeypatch.setattr(media_sync, "reconcile_category_media_snapshot", reconcile)

    result = await media_sync.synchronize_media_catalog()

    assert_that(result.acquired, is_(True))
    assert_that(result.categories, equal_to(1))
    assert_that(result.failed, equal_to(1))
    assert_that(result, has_properties(discovered=1, created=1))
    assert_that(reconcile.await_args_list, has_length(1))
    source = reconcile.await_args_list[0].kwargs["sources"][0]
    assert_that(source.telegram_media_type, same_instance(TelegramMediaType.animation))
    delete_lease.assert_awaited_once()


async def test_lost_lease_does_not_publish_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_sync,
        "get_all_subscription_types",
        AsyncMock(return_value=[_subscription_type(1, "/day", "day")]),
    )
    monkeypatch.setattr(media_sync.cache, "set_if_absent", AsyncMock(return_value=True))
    monkeypatch.setattr(media_sync.cache, "refresh_if_value", AsyncMock(return_value=False))
    monkeypatch.setattr(media_sync.cache, "delete_if_value", AsyncMock(return_value=False))

    async def list_images(directory: str):
        yield _image(f"{directory}/image.png")

    monkeypatch.setattr(media_sync, "list_dir", list_images)
    reconcile = AsyncMock()
    monkeypatch.setattr(media_sync, "reconcile_category_media_snapshot", reconcile)

    result = await media_sync.synchronize_media_catalog()

    assert_that(result.acquired, is_(True))
    assert_that(result.categories, equal_to(0))
    assert_that(result.failed, equal_to(1))
    reconcile.assert_not_awaited()


@pytest.mark.parametrize("interval", [timedelta(0), timedelta(seconds=-1)])
async def test_runner_rejects_non_positive_interval(interval: timedelta) -> None:
    with pytest.raises(ValueError, match="positive"):
        await media_sync.run_media_sync(interval=interval)


def _subscription_type(subscription_type_id: int, name: str, directory: str) -> SubscriptionType:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return SubscriptionType(
        id=subscription_type_id,
        name=name,
        time=time(13),
        s3_directory_path=directory,
        search_aliases=(),
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _image(path: str, *, mime_type: str = "image/png") -> Image:
    return Image(
        name=path.rsplit("/", maxsplit=1)[-1],
        mime_type=mime_type,
        path=path,
        source_revision=f"sha256:{path}",
    )

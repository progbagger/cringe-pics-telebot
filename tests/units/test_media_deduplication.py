import asyncio
from collections.abc import AsyncGenerator
from threading import Event
from unittest.mock import AsyncMock

import pytest
from hamcrest import assert_that, contains_exactly, equal_to, has_properties

from cringe_pics_telebot.repositories.yandex import DownloadedFile, Image, YandexDestinationExistsError
from cringe_pics_telebot.services import media_deduplication
from cringe_pics_telebot.services.media_deduplication import (
    MediaDeduplicationAnalysisError,
    MediaDeduplicationDownloadError,
    MediaDeduplicationLimitError,
    MediaDeduplicationSettings,
)
from cringe_pics_telebot.services.media_duplicate_detection import ScanResult


async def test_exact_duplicates_are_moved_to_excluded_quarantine_and_relisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = (_image("day/first.gif"), _image("day/copy.gif"))
    monkeypatch.setattr(
        media_deduplication,
        "download_file",
        AsyncMock(return_value=DownloadedFile(content=b"same-gif", content_type="image/gif")),
    )
    create_directory = AsyncMock()
    move_resource = AsyncMock()
    ensure_lease = AsyncMock()
    monkeypatch.setattr(media_deduplication, "create_directory", create_directory)
    monkeypatch.setattr(media_deduplication, "move_resource", move_resource)

    async def list_images(directory: str) -> AsyncGenerator[Image]:
        assert directory == "day"
        yield images[0]

    monkeypatch.setattr(media_deduplication, "list_dir", list_images)

    result = await media_deduplication.deduplicate_category_media(
        category_directory="day",
        images=images,
        ensure_lease=ensure_lease,
    )

    assert_that(result, has_properties(groups=1, moved=1, move_failed=0, images=(images[0],)))
    create_directory.assert_awaited_once_with("day/first-duplicates")
    move_resource.assert_awaited_once_with(
        source_path="day/copy.gif",
        destination_path="day/first-duplicates/copy.gif",
    )
    assert ensure_lease.await_count == 2


async def test_destination_collision_uses_numbered_name(monkeypatch: pytest.MonkeyPatch) -> None:
    images = (_image("day/first.gif"), _image("day/copy.gif"))
    monkeypatch.setattr(
        media_deduplication,
        "download_file",
        AsyncMock(return_value=DownloadedFile(content=b"same-gif", content_type="image/gif")),
    )
    monkeypatch.setattr(media_deduplication, "create_directory", AsyncMock())
    move_resource = AsyncMock(side_effect=[YandexDestinationExistsError("occupied"), None])
    monkeypatch.setattr(media_deduplication, "move_resource", move_resource)

    async def list_images(directory: str) -> AsyncGenerator[Image]:
        yield images[0]

    monkeypatch.setattr(media_deduplication, "list_dir", list_images)

    result = await media_deduplication.deduplicate_category_media(
        category_directory="day",
        images=images,
        ensure_lease=AsyncMock(),
    )

    assert result.moved == 1
    assert_that(
        [call.kwargs["destination_path"] for call in move_resource.await_args_list],
        contains_exactly("day/first-duplicates/copy.gif", "day/first-duplicates/copy-2.gif"),
    )


async def test_ambiguous_move_failure_is_counted_and_actual_paths_are_relisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = (_image("day/first.gif"), _image("day/copy.gif"))
    monkeypatch.setattr(
        media_deduplication,
        "download_file",
        AsyncMock(return_value=DownloadedFile(content=b"same-gif", content_type="image/gif")),
    )
    monkeypatch.setattr(media_deduplication, "create_directory", AsyncMock())
    monkeypatch.setattr(media_deduplication, "move_resource", AsyncMock(side_effect=TimeoutError("unknown outcome")))

    async def list_images(directory: str) -> AsyncGenerator[Image]:
        assert directory == "day"
        yield images[0]

    monkeypatch.setattr(media_deduplication, "list_dir", list_images)

    result = await media_deduplication.deduplicate_category_media(
        category_directory="day",
        images=images,
        ensure_lease=AsyncMock(),
    )

    assert_that(result, has_properties(groups=1, moved=0, move_failed=1, images=(images[0],)))


async def test_download_failure_stops_before_storage_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_deduplication, "download_file", AsyncMock(side_effect=RuntimeError("unavailable")))
    create_directory = AsyncMock()
    move_resource = AsyncMock()
    monkeypatch.setattr(media_deduplication, "create_directory", create_directory)
    monkeypatch.setattr(media_deduplication, "move_resource", move_resource)

    with pytest.raises(MediaDeduplicationDownloadError, match="day/first.gif"):
        await media_deduplication.deduplicate_category_media(
            category_directory="day",
            images=(_image("day/first.gif"), _image("day/copy.gif")),
            ensure_lease=AsyncMock(),
        )

    create_directory.assert_not_awaited()
    move_resource.assert_not_awaited()


async def test_visual_analysis_error_stops_before_storage_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_deduplication,
        "download_file",
        AsyncMock(return_value=DownloadedFile(content=b"not-an-image", content_type="image/png")),
    )
    create_directory = AsyncMock()
    monkeypatch.setattr(media_deduplication, "create_directory", create_directory)

    with pytest.raises(MediaDeduplicationAnalysisError, match="Cannot decode image"):
        await media_deduplication.deduplicate_category_media(
            category_directory="day",
            images=(_image("day/first.png", mime_type="image/png"), _image("day/copy.png", mime_type="image/png")),
            ensure_lease=AsyncMock(),
        )

    create_directory.assert_not_awaited()


async def test_declared_category_limit_is_checked_before_download(monkeypatch: pytest.MonkeyPatch) -> None:
    download_file = AsyncMock()
    monkeypatch.setattr(media_deduplication, "download_file", download_file)

    with pytest.raises(MediaDeduplicationLimitError, match="category exceeds"):
        await media_deduplication.deduplicate_category_media(
            category_directory="day",
            images=(_image("day/first.gif", size=6), _image("day/copy.gif", size=6)),
            ensure_lease=AsyncMock(),
            settings=MediaDeduplicationSettings(max_file_bytes=10, max_category_bytes=11),
        )

    download_file.assert_not_awaited()


async def test_cpu_analysis_does_not_block_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    images = (_image("day/first.gif"), _image("day/second.gif"))
    monkeypatch.setattr(
        media_deduplication,
        "download_file",
        AsyncMock(return_value=DownloadedFile(content=b"different", content_type="image/gif")),
    )

    loop = asyncio.get_running_loop()
    analysis_started = asyncio.Event()
    release_analysis = Event()

    def analyze(*args: object, **kwargs: object) -> ScanResult:
        loop.call_soon_threadsafe(analysis_started.set)
        release_analysis.wait(timeout=5)
        return ScanResult(files=(), groups=(), errors=())

    monkeypatch.setattr(media_deduplication, "find_duplicate_groups", analyze)

    task = asyncio.create_task(
        media_deduplication.deduplicate_category_media(
            category_directory="day",
            images=images,
            ensure_lease=AsyncMock(),
        )
    )
    await asyncio.wait_for(analysis_started.wait(), timeout=1)

    event_loop_progressed = asyncio.Event()
    loop.call_soon(event_loop_progressed.set)
    await asyncio.wait_for(event_loop_progressed.wait(), timeout=1)
    assert task.done() is False

    release_analysis.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert_that(result.images, equal_to(images))


def _image(path: str, *, mime_type: str = "image/gif", size: int | None = 8) -> Image:
    return Image(
        name=path.rsplit("/", maxsplit=1)[-1],
        mime_type=mime_type,
        path=path,
        source_revision=f"sha256:{path}",
        size=size,
    )

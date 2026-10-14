from asyncio import subprocess
from collections.abc import Awaitable, Callable
from datetime import time
from hashlib import sha256
from io import BytesIO
from typing import Any

import pytest
from hamcrest import assert_that, contains_string, equal_to
from PIL import Image, ImageDraw

from tests.functional.conftest import (
    FakeStatsDServer,
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)


@pytest.fixture(autouse=True)
async def reset_state_before_test(
    reset_functional_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
) -> None:
    await reset_functional_state(
        (
            FunctionalSubscriptionType(
                id=1,
                name="dedupe",
                send_time=time(13),
                s3_directory_path="dedupe",
            ),
        )
    )


async def test_admin_sync_deduplicates_media_without_blocking_bot(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    fake_statsd_server: FakeStatsDServer,
    set_functional_administrator: Callable[..., Awaitable[None]],
    set_functional_category_media_file_ids: Callable[[dict[str, str | None]], Awaitable[None]],
    read_functional_category_media_states: Callable[[], Awaitable[dict[str, tuple[str, str | None]]]],
) -> None:
    source, visual_variant, unrelated = _image_payloads()
    initial_media = {
        "original.png": ("image/png", source),
        "variant.jpg": ("image/jpeg", unrelated),
        "clip.mp4": ("video/mp4", b"first-video"),
        "clip-copy.mp4": ("video/mp4", b"second-video"),
    }
    await _configure_media(fake_yandex_server, initial_media, blocked_name="original.png")
    await set_functional_administrator(user_id=42)

    await _open_admin_panel(fake_telegram_server)
    await fake_telegram_server.push_callback_query(data="admin_panel:synchronize_media", message_id=501)
    await fake_yandex_server.wait_for_download("original.png")

    await fake_telegram_server.push_message(text="/start", first_name="Admin")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Что умеет бот" in request["payload"].get("text", ""),
    )
    await fake_yandex_server.release_download("original.png")

    first_result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: (
            "<b>Синхронизация медиа завершена</b>" in request["payload"].get("text", "")
            and "Создано записей: <b>4</b>" in request["payload"].get("text", "")
        ),
    )
    assert_that(first_result["payload"]["text"], contains_string("Найдено групп дубликатов: <b>0</b>"))

    await set_functional_category_media_file_ids(
        {
            "dedupe/variant.jpg": "telegram-variant",
            "dedupe/clip-copy.mp4": "telegram-clip-copy",
        }
    )
    await fake_telegram_server.reset()
    await fake_statsd_server.reset()

    duplicate_media = {
        "original.png": ("image/png", source),
        "variant.jpg": ("image/jpeg", visual_variant),
        "clip.mp4": ("video/mp4", b"same-video"),
        "clip-copy.mp4": ("video/mp4", b"same-video"),
    }
    await _configure_media(fake_yandex_server, duplicate_media)
    await _open_admin_panel(fake_telegram_server)
    await fake_telegram_server.push_callback_query(data="admin_panel:synchronize_media", message_id=502)

    result = await fake_telegram_server.wait_for_request(
        "editMessageText",
        predicate=lambda request: "Найдено групп дубликатов: <b>2</b>" in request["payload"].get("text", ""),
    )
    for expected_line in (
        "Найдено медиа: <b>2</b>",
        "Деактивировано отсутствующее медиа: <b>2</b>",
        "Найдено групп дубликатов: <b>2</b>",
        "Перемещено дубликатов: <b>2</b>",
        "Ошибок перемещения дубликатов: <b>0</b>",
    ):
        assert_that(result["payload"]["text"], contains_string(expected_line))

    assert_that(
        await read_functional_category_media_states(),
        equal_to(
            {
                "dedupe/clip-copy.mp4": ("inactive", "telegram-clip-copy"),
                "dedupe/clip.mp4": ("pending", None),
                "dedupe/original.png": ("pending", None),
                "dedupe/variant.jpg": ("inactive", "telegram-variant"),
            }
        ),
    )
    assert_that(
        [item["name"] for item in await fake_yandex_server.directory("dedupe")],
        equal_to(["original.png", "clip.mp4"]),
    )
    assert_that(
        [item["name"] for item in await fake_yandex_server.directory("dedupe/original-duplicates")],
        equal_to(["variant.jpg"]),
    )
    assert_that(
        [item["name"] for item in await fake_yandex_server.directory("dedupe/clip-duplicates")],
        equal_to(["clip-copy.mp4"]),
    )

    move_requests = [
        request for request in await fake_yandex_server.requests() if request["method"] == "resources/move"
    ]
    assert_that(len(move_requests), equal_to(2))
    assert_that({request["params"]["overwrite"] for request in move_requests}, equal_to({"false"}))

    for metric_name, expected_value in (
        ("functional.media_deduplication.groups", 2),
        ("functional.media_deduplication.moved", 2),
        ("functional.media_deduplication.move_failed", 0),
    ):
        metric = await fake_statsd_server.wait_for_metric(metric_name)
        assert_that(metric["value"], equal_to(expected_value))

    assert bot_process.returncode is None


async def _open_admin_panel(fake_telegram_server: FakeTelegramServer) -> None:
    await fake_telegram_server.push_message(text="/admin")
    await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=lambda request: "Админ-панель" in request["payload"].get("text", ""),
    )


async def _configure_media(
    fake_yandex_server: FakeYandexServer,
    media: dict[str, tuple[str, bytes]],
    *,
    blocked_name: str | None = None,
) -> None:
    images: list[dict[str, Any]] = []
    for name, (mime_type, content) in media.items():
        images.append(
            {
                "name": name,
                "mime_type": mime_type,
                "size": len(content),
                "sha256": sha256(content).hexdigest(),
            }
        )
        await fake_yandex_server.configure_download(
            name=name,
            content=content,
            content_type=mime_type,
            block=name == blocked_name,
        )

    await fake_yandex_server.configure_directory("dedupe", images=images)


def _image_payloads() -> tuple[bytes, bytes, bytes]:
    source = Image.new("RGB", (480, 360), "#e8d6b0")
    draw = ImageDraw.Draw(source)
    draw.rectangle((20, 25, 210, 160), fill="#184a70")
    draw.ellipse((250, 60, 430, 240), fill="#cc503e")
    draw.polygon(((60, 300), (220, 190), (350, 330)), fill="#4c956c")
    for x in range(0, source.width, 20):
        draw.line((x, 0, source.width - x // 2, source.height), fill="#f4f1de", width=2)

    source_bytes = _encode_image(source, format_name="PNG")
    visual_variant = source.resize((240, 180), Image.Resampling.LANCZOS)
    visual_variant_bytes = _encode_image(visual_variant, format_name="JPEG", quality=58)
    unrelated_bytes = _encode_image(Image.new("RGB", (240, 180), "#202020"), format_name="JPEG")
    return source_bytes, visual_variant_bytes, unrelated_bytes


def _encode_image(image: Image.Image, *, format_name: str, quality: int | None = None) -> bytes:
    output = BytesIO()
    save_options = {"quality": quality} if quality is not None else {}
    image.save(output, format=format_name, **save_options)
    return output.getvalue()

import asyncio
import logging
from io import BytesIO
from threading import Event

import av
import pytest
from hamcrest import assert_that, equal_to, greater_than, less_than_or_equal_to
from PIL import Image

from cringe_pics_telebot.services import media_alias_images
from cringe_pics_telebot.services.media_alias_images import (
    MediaAliasDecodeError,
    MediaAliasFrameTooLargeError,
    PreparedMediaAliasImageTooLargeError,
    UnsupportedMediaAliasTypeError,
    prepare_media_alias_image,
)


def _image_bytes(
    image_format: str,
    *,
    color: tuple[int, int, int] = (20, 40, 60),
    size: tuple[int, int] = (24, 16),
) -> bytes:
    target = BytesIO()
    image = Image.new("RGB", size, color)
    image.save(target, format=image_format)
    image.close()

    return target.getvalue()


def _gif_bytes() -> bytes:
    target = BytesIO()
    frames = [
        Image.new("RGB", (16, 16), (220, 20, 20)),
        Image.new("RGB", (16, 16), (20, 220, 20)),
        Image.new("RGB", (16, 16), (20, 20, 220)),
    ]
    try:
        frames[0].save(target, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    finally:
        for frame in frames:
            frame.close()

    return target.getvalue()


def _mp4_bytes() -> bytes:
    target = BytesIO()
    with av.open(target, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=1)
        stream.width = 16
        stream.height = 16
        stream.pix_fmt = "yuv420p"

        for color in ((220, 20, 20), (20, 220, 20), (20, 20, 220), (220, 220, 20), (220, 20, 220)):
            image = Image.new("RGB", (16, 16), color)
            try:
                frame = av.VideoFrame.from_image(image)
                for packet in stream.encode(frame):
                    container.mux(packet)
            finally:
                image.close()

        for packet in stream.encode():
            container.mux(packet)

    return target.getvalue()


@pytest.mark.parametrize(
    ("mime_type", "source"),
    [
        ("image/png", _image_bytes("PNG", color=(220, 20, 20))),
        ("image/jpeg", _image_bytes("JPEG", color=(220, 20, 20))),
        ("image/gif", _gif_bytes()),
        ("video/mp4", _mp4_bytes()),
    ],
)
async def test_supported_media_is_prepared_as_bounded_rgb_jpeg(*, mime_type: str, source: bytes) -> None:
    result = await prepare_media_alias_image(
        source=source,
        mime_type=mime_type,
        max_frame_pixels=10_000,
        max_image_edge_pixels=12,
        max_image_bytes=10_000,
    )

    assert_that(len(result), less_than_or_equal_to(10_000))
    with Image.open(BytesIO(result)) as image:
        assert_that(image.format, equal_to("JPEG"))
        assert_that(image.mode, equal_to("RGB"))
        assert_that(max(image.size), less_than_or_equal_to(12))


async def test_gif_uses_middle_frame() -> None:
    result = await prepare_media_alias_image(
        source=_gif_bytes(),
        mime_type="image/gif",
        max_frame_pixels=10_000,
        max_image_edge_pixels=32,
        max_image_bytes=10_000,
    )

    with Image.open(BytesIO(result)) as image:
        red, green, blue = image.getpixel((8, 8))
    assert_that(green, greater_than(red))
    assert_that(green, greater_than(blue))


async def test_mp4_uses_frame_around_middle() -> None:
    result = await prepare_media_alias_image(
        source=_mp4_bytes(),
        mime_type="video/mp4",
        max_frame_pixels=10_000,
        max_image_edge_pixels=32,
        max_image_bytes=10_000,
    )

    with Image.open(BytesIO(result)) as image:
        red, green, blue = image.getpixel((8, 8))
    assert_that(blue, greater_than(red))
    assert_that(blue, greater_than(green))


async def test_mp4_without_usable_middle_falls_back_to_first_decodable_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_alias_images, "_decode_video_frame_at_middle", lambda *args, **kwargs: None)
    result = await prepare_media_alias_image(
        source=_mp4_bytes(),
        mime_type="video/mp4",
        max_frame_pixels=10_000,
        max_image_edge_pixels=32,
        max_image_bytes=10_000,
    )

    with Image.open(BytesIO(result)) as image:
        red, green, blue = image.getpixel((8, 8))
    assert_that(red, greater_than(green))
    assert_that(red, greater_than(blue))


async def test_prepared_byte_limit_reduces_quality_and_dimensions() -> None:
    with (
        Image.frombytes("RGB", (128, 64), bytes((index * 37) % 256 for index in range(128 * 64 * 3))) as image,
        BytesIO() as encoded,
    ):
        image.save(encoded, format="PNG")
        source = encoded.getvalue()

    result = await prepare_media_alias_image(
        source=source,
        mime_type="image/png",
        max_frame_pixels=10_000,
        max_image_edge_pixels=128,
        max_image_bytes=400,
    )

    assert len(result) <= 400
    with Image.open(BytesIO(result)) as image:
        assert image.width < 128


async def test_photo_applies_exif_orientation() -> None:
    source = BytesIO()
    image = Image.new("RGB", (12, 6), (100, 120, 140))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, format="JPEG", exif=exif)
    image.close()

    result = await prepare_media_alias_image(
        source=source.getvalue(),
        mime_type="image/jpeg",
        max_frame_pixels=10_000,
        max_image_edge_pixels=32,
        max_image_bytes=10_000,
    )

    with Image.open(BytesIO(result)) as prepared:
        assert_that(prepared.size, equal_to((6, 12)))


@pytest.mark.parametrize(
    ("source", "mime_type", "max_frame_pixels", "max_image_bytes", "error_type"),
    [
        (b"not-an-image", "image/png", 10_000, 10_000, MediaAliasDecodeError),
        (_image_bytes("PNG"), "image/gif", 10_000, 10_000, MediaAliasDecodeError),
        (b"not-a-video", "video/mp4", 10_000, 10_000, MediaAliasDecodeError),
        (b"anything", "application/octet-stream", 10_000, 10_000, UnsupportedMediaAliasTypeError),
        (_image_bytes("PNG", size=(20, 20)), "image/png", 399, 10_000, MediaAliasFrameTooLargeError),
        (_gif_bytes(), "image/gif", 255, 10_000, MediaAliasFrameTooLargeError),
        (_mp4_bytes(), "video/mp4", 255, 10_000, MediaAliasFrameTooLargeError),
        (_image_bytes("PNG"), "image/png", 10_000, 1, PreparedMediaAliasImageTooLargeError),
    ],
)
async def test_invalid_or_oversized_media_is_rejected(
    *,
    source: bytes,
    mime_type: str,
    max_frame_pixels: int,
    max_image_bytes: int,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        await prepare_media_alias_image(
            source=source,
            mime_type=mime_type,
            max_frame_pixels=max_frame_pixels,
            max_image_edge_pixels=32,
            max_image_bytes=max_image_bytes,
        )


async def test_cancellation_waits_for_started_preparation_cleanup(
    *,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = Event()
    cancellation_logs: asyncio.Queue[logging.LogRecord] = asyncio.Queue()

    class CancellationLogHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            cancellation_logs.put_nowait(record)

    def prepare(**kwargs: object) -> bytes:
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=5)
        return b"prepared"

    monkeypatch.setattr(media_alias_images, "_prepare_media_alias_image", prepare)
    task = asyncio.create_task(
        prepare_media_alias_image(
            source=b"source",
            mime_type="image/png",
            max_frame_pixels=1,
            max_image_edge_pixels=1,
            max_image_bytes=1,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    handler = CancellationLogHandler()
    media_alias_images.logger.addHandler(handler)
    caplog.set_level(logging.INFO, logger=media_alias_images.__name__)
    try:
        task.cancel()
        record = await asyncio.wait_for(cancellation_logs.get(), timeout=1)
        assert "waiting for decoder thread cleanup" in record.getMessage()
        assert task.done() is False

        task.cancel()
        record = await asyncio.wait_for(cancellation_logs.get(), timeout=1)
        assert "Repeated cancellation" in record.getMessage()
        assert task.done() is False
    finally:
        release.set()
        media_alias_images.logger.removeHandler(handler)

    with pytest.raises(asyncio.CancelledError):
        await task

    assert "decoder thread cleanup completed" in caplog.text

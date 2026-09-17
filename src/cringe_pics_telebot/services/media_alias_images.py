import asyncio
import logging
from contextlib import suppress
from fractions import Fraction
from io import BytesIO

import av
from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.GifImagePlugin import GifImageFile

logger = logging.getLogger(__name__)
_JPEG_QUALITIES = (90, 80, 70, 60, 50, 40, 30)
_RESIZE_FACTOR = 0.75
_CANCELLATION_LOG_INTERVAL_SECONDS = 5


class MediaAliasImagePreparationError(ValueError): ...


class UnsupportedMediaAliasTypeError(MediaAliasImagePreparationError): ...


class MediaAliasDecodeError(MediaAliasImagePreparationError): ...


class MediaAliasFrameTooLargeError(MediaAliasImagePreparationError): ...


class PreparedMediaAliasImageTooLargeError(MediaAliasImagePreparationError): ...


async def prepare_media_alias_image(
    *,
    source: bytes,
    mime_type: str,
    max_frame_pixels: int,
    max_image_edge_pixels: int,
    max_image_bytes: int,
) -> bytes:
    if max_frame_pixels <= 0:
        raise ValueError("Media alias frame pixel limit must be positive")
    if max_image_edge_pixels <= 0:
        raise ValueError("Media alias image edge limit must be positive")
    if max_image_bytes <= 0:
        raise ValueError("Media alias prepared image byte limit must be positive")

    preparation = asyncio.create_task(
        asyncio.to_thread(
            _prepare_media_alias_image,
            source=source,
            mime_type=mime_type,
            max_frame_pixels=max_frame_pixels,
            max_image_edge_pixels=max_image_edge_pixels,
            max_image_bytes=max_image_bytes,
        )
    )

    try:
        return await asyncio.shield(preparation)
    except asyncio.CancelledError:
        cleanup_started_at = asyncio.get_running_loop().time()
        logger.warning("Media alias image preparation cancelled; waiting for decoder thread cleanup")

        # Cancelling to_thread does not stop the decoder or close its resources.
        while not preparation.done():
            try:
                done, _ = await asyncio.wait({preparation}, timeout=_CANCELLATION_LOG_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                logger.warning("Repeated cancellation while waiting for media alias decoder thread cleanup")
                continue
            if not done:
                logger.warning(
                    "Still waiting for media alias decoder thread cleanup after %.1f seconds",
                    asyncio.get_running_loop().time() - cleanup_started_at,
                )

        with suppress(Exception):
            preparation.result()

        logger.info(
            "Media alias decoder thread cleanup completed after %.1f seconds",
            asyncio.get_running_loop().time() - cleanup_started_at,
        )
        raise


def _prepare_media_alias_image(
    *,
    source: bytes,
    mime_type: str,
    max_frame_pixels: int,
    max_image_edge_pixels: int,
    max_image_bytes: int,
) -> bytes:
    if not source:
        raise MediaAliasDecodeError("Media source is empty")

    if mime_type == "video/mp4":
        image = _decode_video_frame(source=source, max_frame_pixels=max_frame_pixels)
    elif mime_type == "image/gif":
        image = _decode_pillow_frame(source=source, middle_frame=True, max_frame_pixels=max_frame_pixels)
    elif mime_type.startswith("image/"):
        image = _decode_pillow_frame(source=source, middle_frame=False, max_frame_pixels=max_frame_pixels)
    else:
        raise UnsupportedMediaAliasTypeError(f"Unsupported media type for alias enrichment: {mime_type}")

    try:
        return _encode_bounded_jpeg(
            image=image,
            max_edge_pixels=max_image_edge_pixels,
            max_bytes=max_image_bytes,
        )
    except (OSError, ValueError) as error:
        if isinstance(error, MediaAliasImagePreparationError):
            raise
        raise MediaAliasDecodeError("Failed to encode image for alias enrichment") from error
    finally:
        image.close()


def _decode_pillow_frame(*, source: bytes, middle_frame: bool, max_frame_pixels: int) -> Image.Image:
    try:
        with BytesIO(source) as buffer, Image.open(buffer) as opened:
            _validate_frame_dimensions(width=opened.width, height=opened.height, max_frame_pixels=max_frame_pixels)
            if middle_frame:
                if not isinstance(opened, GifImageFile):
                    raise MediaAliasDecodeError("Media source declared as GIF is not a GIF image")

                opened.seek(opened.n_frames // 2)
                _validate_frame_dimensions(width=opened.width, height=opened.height, max_frame_pixels=max_frame_pixels)
                return opened.convert("RGB")

            oriented = ImageOps.exif_transpose(opened)
            try:
                _validate_frame_dimensions(
                    width=oriented.width, height=oriented.height, max_frame_pixels=max_frame_pixels
                )
                return oriented.convert("RGB")
            finally:
                if oriented is not opened:
                    oriented.close()
    except MediaAliasImagePreparationError:
        raise
    except Image.DecompressionBombError as error:
        raise MediaAliasFrameTooLargeError("Media frame exceeds decoder pixel safety limit") from error
    except (UnidentifiedImageError, OSError, ValueError, EOFError) as error:
        raise MediaAliasDecodeError("Failed to decode image for alias enrichment") from error


def _decode_video_frame(*, source: bytes, max_frame_pixels: int) -> Image.Image:
    try:
        image = _decode_video_frame_at_middle(source=source, max_frame_pixels=max_frame_pixels)
        if image is not None:
            return image

        image = _decode_first_video_frame(source=source, max_frame_pixels=max_frame_pixels)
        if image is None:
            raise MediaAliasDecodeError("Video has no decodable frames")

        return image
    except MediaAliasImagePreparationError:
        raise
    except Exception as error:
        raise MediaAliasDecodeError("Failed to decode video for alias enrichment") from error


def _decode_video_frame_at_middle(*, source: bytes, max_frame_pixels: int) -> Image.Image | None:
    with BytesIO(source) as buffer, av.open(buffer, mode="r") as container:
        stream = next(iter(container.streams.video), None)
        if stream is None or stream.time_base is None:
            return None

        if stream.duration is not None and stream.duration > 0:
            target_pts = (stream.start_time or 0) + stream.duration // 2
        elif container.duration is not None and container.duration > 0:
            target_pts = (stream.start_time or 0) + int(
                Fraction(container.duration, av.time_base) / stream.time_base / 2
            )
        else:
            return None

        if stream.width > 0 and stream.height > 0:
            _validate_frame_dimensions(width=stream.width, height=stream.height, max_frame_pixels=max_frame_pixels)

        try:
            container.seek(target_pts, stream=stream, backward=True, any_frame=False)
        except av.error.FFmpegError:
            return None

        selected = None
        for frame in container.decode(stream):
            if frame.pts is None:
                selected = frame
                break
            if frame.pts >= target_pts:
                if selected is None or frame.pts - target_pts < target_pts - (selected.pts or 0):
                    selected = frame
                break
            selected = frame

        if selected is None:
            return None
        _validate_frame_dimensions(width=selected.width, height=selected.height, max_frame_pixels=max_frame_pixels)
        return selected.to_image()


def _decode_first_video_frame(*, source: bytes, max_frame_pixels: int) -> Image.Image | None:
    with BytesIO(source) as buffer, av.open(buffer, mode="r") as container:
        stream = next(iter(container.streams.video), None)
        if stream is None:
            return None

        if stream.width > 0 and stream.height > 0:
            _validate_frame_dimensions(width=stream.width, height=stream.height, max_frame_pixels=max_frame_pixels)

        frame = next(iter(container.decode(stream)), None)
        if frame is None:
            return None

        _validate_frame_dimensions(width=frame.width, height=frame.height, max_frame_pixels=max_frame_pixels)
        return frame.to_image()


def _validate_frame_dimensions(*, width: int, height: int, max_frame_pixels: int) -> None:
    if width <= 0 or height <= 0:
        raise MediaAliasDecodeError("Media frame has invalid dimensions")
    if width * height > max_frame_pixels:
        raise MediaAliasFrameTooLargeError(f"Media frame exceeds pixel limit: {width}x{height} > {max_frame_pixels}")


def _encode_bounded_jpeg(*, image: Image.Image, max_edge_pixels: int, max_bytes: int) -> bytes:
    working = image.copy()
    try:
        working.thumbnail((max_edge_pixels, max_edge_pixels), Image.Resampling.LANCZOS)
        while True:
            for quality in _JPEG_QUALITIES:
                with BytesIO() as encoded:
                    working.save(encoded, format="JPEG", quality=quality, optimize=True, progressive=False)
                    value = encoded.getvalue()
                if len(value) <= max_bytes:
                    return value

            if working.width == 1 and working.height == 1:
                raise PreparedMediaAliasImageTooLargeError(f"Prepared image cannot fit byte limit: {max_bytes}")

            resized = working.resize(
                (
                    max(1, int(working.width * _RESIZE_FACTOR)),
                    max(1, int(working.height * _RESIZE_FACTOR)),
                ),
                Image.Resampling.LANCZOS,
            )
            working.close()
            working = resized
    finally:
        working.close()

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from cringe_pics_telebot.repositories.postgres import CategoryMedia

MAX_PAGE_SIZE = 50
MAX_OFFSET_BYTES = 64
CURSOR_VERSION = 1

_FIRST_PAGE_ORDINARY_SIZE = MAX_PAGE_SIZE - 1
_SEED_SIZE = 8
_QUERY_DIGEST_SIZE = 8
_CURSOR_STRUCT = struct.Struct(">B8sI8s")


type SeedFactory = Callable[[], bytes]


class InvalidInlinePaginationCursor(ValueError):
    """Ошибка восстановления безопасной позиции inline-пагинации."""


@dataclass(frozen=True, slots=True)
class InlinePaginationCursor:
    seed: bytes
    offset: int


@dataclass(frozen=True, slots=True)
class InlineMediaPage:
    special_media: CategoryMedia | None
    ordinary_media: tuple[CategoryMedia, ...]
    next_cursor: InlinePaginationCursor | None
    is_first_page: bool


def paginate_inline_media(
    media: Sequence[CategoryMedia],
    cursor: InlinePaginationCursor | None,
    *,
    seed_factory: SeedFactory | None = None,
) -> InlineMediaPage:
    """Выбирает детерминированную страницу из дедуплицированного каталога."""
    if cursor is None:
        is_first_page = True
        seed = (seed_factory or _new_seed)()
        _validate_seed(seed)
        position = 0
    else:
        is_first_page = False
        seed = cursor.seed
        position = cursor.offset

    if not media:
        if not is_first_page:
            raise InvalidInlinePaginationCursor("cursor position is outside the ordinary media sequence")
        return InlineMediaPage(special_media=None, ordinary_media=(), next_cursor=None, is_first_page=True)

    special_media = _select_special_media(media, seed)
    ordinary_sequence = tuple(
        sorted(
            (item for item in media if item.source_path != special_media.source_path),
            key=lambda item: (
                item.telegram_file_id is None,
                _media_rank(seed, item.source_path),
                item.source_path,
            ),
        )
    )

    if not is_first_page:
        _validate_position(position, len(ordinary_sequence))

    page_size = _FIRST_PAGE_ORDINARY_SIZE if is_first_page else MAX_PAGE_SIZE
    page_end = min(position + page_size, len(ordinary_sequence))
    ordinary_media = ordinary_sequence[position:page_end]
    next_cursor = InlinePaginationCursor(seed=seed, offset=page_end) if page_end < len(ordinary_sequence) else None
    return InlineMediaPage(
        special_media=special_media if is_first_page else None,
        ordinary_media=ordinary_media,
        next_cursor=next_cursor,
        is_first_page=is_first_page,
    )


def _new_seed() -> bytes:
    return secrets.token_bytes(_SEED_SIZE)


def _validate_seed(seed: bytes) -> None:
    if len(seed) != _SEED_SIZE:
        raise ValueError(f"seed factory must return exactly {_SEED_SIZE} bytes")


def _select_special_media(media: Sequence[CategoryMedia], seed: bytes) -> CategoryMedia:
    ready_media = [item for item in media if item.telegram_file_id is not None]
    candidates = ready_media or media
    return min(candidates, key=lambda item: (_media_rank(seed, item.source_path), item.source_path))


def _media_rank(seed: bytes, source_path: str) -> bytes:
    return hashlib.blake2b(seed + source_path.encode("utf-8"), digest_size=16).digest()


def _query_digest(normalized_query: str) -> bytes:
    return hashlib.blake2b(normalized_query.encode("utf-8"), digest_size=_QUERY_DIGEST_SIZE).digest()


def encode_inline_pagination_cursor(cursor: InlinePaginationCursor, normalized_query: str) -> str:
    """Кодирует внутренний cursor для Telegram Bot API."""
    raw_cursor = _CURSOR_STRUCT.pack(CURSOR_VERSION, cursor.seed, cursor.offset, _query_digest(normalized_query))
    encoded = base64.urlsafe_b64encode(raw_cursor).rstrip(b"=")
    if len(encoded) > MAX_OFFSET_BYTES:
        raise RuntimeError("encoded inline pagination cursor exceeds Telegram offset limit")
    return encoded.decode("ascii")


def decode_inline_pagination_cursor(offset: str, normalized_query: str) -> InlinePaginationCursor:
    """Проверяет и декодирует cursor из Telegram Bot API."""
    try:
        encoded = offset.encode("ascii")
    except UnicodeEncodeError as error:
        raise InvalidInlinePaginationCursor("cursor must contain only ASCII characters") from error

    if len(encoded) > MAX_OFFSET_BYTES:
        raise InvalidInlinePaginationCursor("cursor exceeds Telegram offset limit")

    padding = b"=" * (-len(encoded) % 4)
    try:
        raw_cursor = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidInlinePaginationCursor("cursor is not valid URL-safe base64") from error

    if base64.urlsafe_b64encode(raw_cursor).rstrip(b"=") != encoded:
        raise InvalidInlinePaginationCursor("cursor is not canonical unpadded URL-safe base64")
    if len(raw_cursor) != _CURSOR_STRUCT.size:
        raise InvalidInlinePaginationCursor("cursor has an invalid binary structure")

    version, seed, position, query_digest = cast(
        tuple[int, bytes, int, bytes],
        _CURSOR_STRUCT.unpack(raw_cursor),
    )
    if version != CURSOR_VERSION:
        raise InvalidInlinePaginationCursor(f"unsupported cursor version: {version}")
    if not hmac.compare_digest(query_digest, _query_digest(normalized_query)):
        raise InvalidInlinePaginationCursor("cursor does not match the normalized query")

    return InlinePaginationCursor(seed=seed, offset=position)


def _validate_position(position: int, ordinary_count: int) -> None:
    is_page_boundary = (
        position >= _FIRST_PAGE_ORDINARY_SIZE and (position - _FIRST_PAGE_ORDINARY_SIZE) % MAX_PAGE_SIZE == 0
    )
    if not is_page_boundary or position >= ordinary_count:
        raise InvalidInlinePaginationCursor("cursor position is invalid for the ordinary media sequence")

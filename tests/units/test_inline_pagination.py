import base64
import struct
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from hamcrest import assert_that, contains_inanyorder, empty, equal_to, has_length, none

from cringe_pics_telebot.repositories.postgres import CategoryMedia, CategoryMediaStatus, TelegramMediaType
from cringe_pics_telebot.services.inline_pagination import (
    CURSOR_VERSION,
    MAX_OFFSET_BYTES,
    InlineMediaPage,
    InlinePaginationCursor,
    InvalidInlinePaginationCursor,
    decode_inline_pagination_cursor,
    encode_inline_pagination_cursor,
    paginate_inline_media,
)

_QUERY = "morning"
_SEED = b"seed-001"
_CURSOR_STRUCT = struct.Struct(">B8sI8s")


@pytest.mark.parametrize(
    ("media_count", "ordinary_count", "has_next_offset"),
    [
        (0, 0, False),
        (1, 0, False),
        (49, 48, False),
        (50, 49, False),
        (51, 49, True),
    ],
)
def test_first_page_boundaries(media_count: int, ordinary_count: int, has_next_offset: bool) -> None:
    page = paginate_inline_media(_media_items(media_count), None, seed_factory=lambda: _SEED)

    if media_count == 0:
        assert_that(page.special_media, none())
    else:
        assert isinstance(page.special_media, CategoryMedia)
    assert_that(page.ordinary_media, has_length(ordinary_count))
    assert_that(page.next_cursor is not None, equal_to(has_next_offset))
    assert_that(page.is_first_page, equal_to(True))
    assert_that(len(page.ordinary_media) + (page.special_media is not None) <= 50, equal_to(True))


def test_fifty_one_items_continue_with_one_ordinary_item() -> None:
    media = _media_items(51)
    first_page = paginate_inline_media(media, None, seed_factory=lambda: _SEED)
    second_page = paginate_inline_media(
        media,
        _required_cursor(first_page.next_cursor),
        seed_factory=_unexpected_seed,
    )

    assert_that(second_page.special_media, none())
    assert_that(second_page.ordinary_media, has_length(1))
    assert_that(second_page.next_cursor, none())
    assert_that(second_page.is_first_page, equal_to(False))


def test_multiple_pages_have_no_repeats_skips_or_special_media_in_ordinary_sequence() -> None:
    media = _media_items(202)
    pages = list(_all_pages(media))
    special_media = pages[0].special_media
    ordinary_paths = [item.source_path for page in pages for item in page.ordinary_media]

    assert_that([len(page.ordinary_media) for page in pages], equal_to([49, 50, 50, 50, 2]))
    assert_that([page.special_media for page in pages[1:]], equal_to([None, None, None, None]))
    assert special_media is not None
    assert_that(special_media.source_path not in ordinary_paths, equal_to(True))
    assert_that(ordinary_paths, has_length(len(set(ordinary_paths))))
    assert_that(
        ordinary_paths,
        contains_inanyorder(*(item.source_path for item in media if item.source_path != special_media.source_path)),
    )
    assert_that(pages[-1].next_cursor, none())


def test_first_page_uses_only_ready_media_when_ready_media_fills_the_limit() -> None:
    media = [*[_media(index, file_id=f"telegram-{index}") for index in range(50)], _media(50)]

    page = paginate_inline_media(media, None, seed_factory=lambda: _SEED)

    assert page.special_media is not None
    assert_that(page.special_media.telegram_file_id is not None, equal_to(True))
    assert_that([item.telegram_file_id is not None for item in page.ordinary_media], equal_to([True] * 49))
    assert_that(page.next_cursor is not None, equal_to(True))


def test_special_media_prefers_telegram_file_id_and_is_removed_from_all_ordinary_pages() -> None:
    media = [_media(0), _media(1, file_id="telegram-1"), *_media_items(100, start=2)]
    pages = list(_all_pages(media))
    special_media = pages[0].special_media

    assert special_media is not None
    assert_that(special_media.telegram_file_id, equal_to("telegram-1"))
    assert_that(
        [item for page in pages for item in page.ordinary_media if item.source_path == special_media.source_path],
        empty(),
    )


def test_empty_offset_uses_injected_seed_factory_for_each_new_first_request() -> None:
    seeds = iter((b"seed-001", b"seed-002"))
    used_seeds: list[bytes] = []

    def next_seed() -> bytes:
        seed = next(seeds)
        used_seeds.append(seed)
        return seed

    first_page = paginate_inline_media(_media_items(51), None, seed_factory=next_seed)
    next_first_page = paginate_inline_media(_media_items(51), None, seed_factory=next_seed)

    assert_that(used_seeds, equal_to([b"seed-001", b"seed-002"]))
    assert_that(_required_cursor(first_page.next_cursor).seed, equal_to(b"seed-001"))
    assert_that(_required_cursor(next_first_page.next_cursor).seed, equal_to(b"seed-002"))


def test_nonempty_offset_restores_seed_without_calling_seed_factory() -> None:
    media = _media_items(51)
    first_page = paginate_inline_media(media, None, seed_factory=lambda: _SEED)

    page = paginate_inline_media(
        media,
        _required_cursor(first_page.next_cursor),
        seed_factory=_unexpected_seed,
    )

    assert_that(page.ordinary_media, has_length(1))


def test_cursor_is_stable_compact_versioned_and_unpadded() -> None:
    media = _media_items(51)
    first_page = paginate_inline_media(media, None, seed_factory=lambda: _SEED)
    repeated_page = paginate_inline_media(media, None, seed_factory=lambda: _SEED)
    cursor = _required_cursor(first_page.next_cursor)
    offset = encode_inline_pagination_cursor(cursor, _QUERY)
    raw_cursor = _decode_raw_cursor(offset)

    assert_that(first_page, equal_to(repeated_page))
    assert_that(decode_inline_pagination_cursor(offset, _QUERY), equal_to(cursor))
    assert_that(len(offset.encode("ascii")) <= MAX_OFFSET_BYTES, equal_to(True))
    assert_that("=" not in offset, equal_to(True))
    assert_that(len(base64.urlsafe_b64decode(offset)), equal_to(_CURSOR_STRUCT.size))
    assert_that(raw_cursor[0], equal_to(CURSOR_VERSION))
    assert_that(raw_cursor[1], equal_to(_SEED))
    assert_that(raw_cursor[2], equal_to(49))


@pytest.mark.parametrize(
    "offset",
    [
        "not valid base64!",
        base64.urlsafe_b64encode(b"short").rstrip(b"=").decode("ascii"),
        "é",
    ],
)
def test_rejects_malformed_cursor(offset: str) -> None:
    with pytest.raises(InvalidInlinePaginationCursor):
        decode_inline_pagination_cursor(offset, _QUERY)


def test_rejects_oversized_cursor() -> None:
    with pytest.raises(InvalidInlinePaginationCursor, match="exceeds Telegram offset limit"):
        decode_inline_pagination_cursor("a" * (MAX_OFFSET_BYTES + 1), _QUERY)


def test_rejects_padded_cursor_instead_of_accepting_noncanonical_encoding() -> None:
    offset = encode_inline_pagination_cursor(InlinePaginationCursor(seed=_SEED, offset=49), _QUERY)

    with pytest.raises(InvalidInlinePaginationCursor):
        decode_inline_pagination_cursor(f"{offset}=", _QUERY)


def test_rejects_unsupported_cursor_version() -> None:
    offset = encode_inline_pagination_cursor(InlinePaginationCursor(seed=_SEED, offset=49), _QUERY)
    _, seed, position, digest = _decode_raw_cursor(offset)
    unsupported_offset = _encode_raw_cursor(CURSOR_VERSION + 1, seed, position, digest)

    with pytest.raises(InvalidInlinePaginationCursor, match="unsupported cursor version"):
        decode_inline_pagination_cursor(unsupported_offset, _QUERY)


def test_rejects_cursor_bound_to_another_normalized_query() -> None:
    offset = encode_inline_pagination_cursor(InlinePaginationCursor(seed=_SEED, offset=49), _QUERY)

    with pytest.raises(InvalidInlinePaginationCursor, match="does not match"):
        decode_inline_pagination_cursor(offset, "evening")


@pytest.mark.parametrize("offset", [0, 48, 50, 51, 99])
def test_rejects_invalid_cursor_offset(offset: int) -> None:
    media = _media_items(51)
    cursor = InlinePaginationCursor(seed=_SEED, offset=offset)

    with pytest.raises(InvalidInlinePaginationCursor, match="position"):
        paginate_inline_media(media, cursor)


def test_rejects_seed_factory_value_that_is_not_exactly_eight_bytes() -> None:
    with pytest.raises(ValueError, match="exactly 8 bytes"):
        paginate_inline_media(_media_items(1), None, seed_factory=lambda: b"short")


def _all_pages(media: list[CategoryMedia]) -> Iterator[InlineMediaPage]:
    cursor: InlinePaginationCursor | None = None
    while True:
        page = paginate_inline_media(media, cursor, seed_factory=lambda: _SEED)
        yield page
        if page.next_cursor is None:
            return
        cursor = page.next_cursor


def _required_cursor(cursor: InlinePaginationCursor | None) -> InlinePaginationCursor:
    assert cursor is not None
    return cursor


def _decode_raw_cursor(offset: str) -> tuple[int, bytes, int, bytes]:
    padding = "=" * (-len(offset) % 4)
    return _CURSOR_STRUCT.unpack(base64.urlsafe_b64decode(offset + padding))


def _encode_raw_cursor(version: int, seed: bytes, position: int, digest: bytes) -> str:
    return base64.urlsafe_b64encode(_CURSOR_STRUCT.pack(version, seed, position, digest)).rstrip(b"=").decode("ascii")


def _unexpected_seed() -> bytes:
    raise AssertionError("seed factory must not be called for a continuation cursor")


def _media_items(count: int, *, start: int = 0) -> list[CategoryMedia]:
    return [_media(index) for index in range(start, start + count)]


def _media(media_id: int, *, file_id: str | None = None) -> CategoryMedia:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    return CategoryMedia(
        id=media_id,
        subscription_type_id=1,
        source_path=f"media/{media_id}.png",
        source_revision=f"sha256:{media_id}",
        name=f"{media_id}.png",
        mime_type="image/png",
        telegram_media_type=TelegramMediaType.photo,
        telegram_file_id=file_id,
        telegram_file_unique_id=f"unique-{media_id}" if file_id is not None else None,
        is_active=True,
        status=CategoryMediaStatus.ready if file_id is not None else CategoryMediaStatus.pending,
        last_seen_at=now,
        materialized_at=now if file_id is not None else None,
        created_at=now,
        updated_at=now,
    )

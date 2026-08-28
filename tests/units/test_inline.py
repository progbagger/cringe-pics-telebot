import asyncio
import json
import logging
from datetime import UTC, datetime, time
from typing import Any, cast

import pytest
from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultUnion,
)
from hamcrest import assert_that, empty, equal_to, has_length, is_, not_
from pytest import MonkeyPatch

from cringe_pics_telebot.bot import inline, keyboards
from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.services.inline_images import InlineImagesPage
from cringe_pics_telebot.services.inline_pagination import (
    InlinePaginationCursor,
    encode_inline_pagination_cursor,
)
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia


@pytest.mark.parametrize(
    ("query", "category", "search_aliases", "matches"),
    [
        ("day", "/day", (), True),
        ("  DA ", "/day", (), True),
        ("/d", "/day", (), True),
        ("ING", "/evening", (), True),
        ("  УТР  ", "/morning", ("утро", "утренняя", "с утра"), True),
        ("/ВЕЧЕРН", "/evening", ("вечер", "  Вечерняя "), True),
        ("днев", "/day", ("", "   ", "/", "дневная", " ДНЕВНАЯ "), True),
        ("", "/day", ("день",), False),
        (" / ", "/day", ("день",), False),
        ("night", "/day", ("день",), False),
    ],
)
def test_category_matches_query(
    query: str,
    category: str,
    search_aliases: tuple[str, ...],
    matches: bool,
) -> None:
    assert_that(inline.category_matches_query(query, category, search_aliases), is_(matches))


@pytest.mark.parametrize("query", ["", "   ", " / "])
async def test_find_subscription_types_returns_all_categories_for_normalized_empty_query(
    monkeypatch: MonkeyPatch,
    query: str,
) -> None:
    subscription_types = [
        _subscription_type(1, "/morning", "morning"),
        _subscription_type(2, "/day", "day"),
    ]
    monkeypatch.setattr(inline, "get_subscription_types", lambda: _async_result(subscription_types))

    assert_that(await inline._find_subscription_types(query), equal_to(subscription_types))


async def test_find_subscription_types_returns_every_matching_category(monkeypatch: MonkeyPatch) -> None:
    subscription_types = [
        _subscription_type(1, "/morning", "morning", search_aliases=("утро", " общее ", "/ОБЩЕЕ")),
        _subscription_type(2, "/day", "day"),
        _subscription_type(3, "/evening", "evening", search_aliases=("вечер", "общее")),
        _subscription_type(4, "/night", "night"),
    ]

    async def get_subscription_types() -> list[SubscriptionType]:
        return subscription_types

    monkeypatch.setattr(inline, "get_subscription_types", get_subscription_types)

    assert_that(
        await inline._find_subscription_types(" /ОБЩ "),
        equal_to([subscription_types[0], subscription_types[2]]),
    )


async def test_get_inline_results_combines_categories_without_duplicate_paths(monkeypatch: MonkeyPatch) -> None:
    morning = _subscription_type(1, "/morning", "morning")
    evening = _subscription_type(3, "/evening", "evening")

    expected_cursor = InlinePaginationCursor(seed=b"seed-001", offset=49)
    next_cursor = InlinePaginationCursor(seed=b"seed-001", offset=99)

    async def get_inline_images(
        subscription_types: list[SubscriptionType],
        *,
        cursor: InlinePaginationCursor | None,
    ) -> InlineImagesPage:
        assert_that(subscription_types, equal_to([morning, evening]))
        assert_that(cursor, equal_to(expected_cursor))
        images: list[tuple[SubscriptionType, CachedMedia | LinkedMedia]] = [
            (
                morning,
                CachedMedia(
                    name="shared.png",
                    mime_type="image/png",
                    path="shared/image.png",
                    source_revision="sha256:shared",
                    id="telegram-shared",
                ),
            ),
            (
                morning,
                LinkedMedia(
                    name="morning.png",
                    mime_type="image/png",
                    path="morning/image.png",
                    source_revision="sha256:morning",
                    url="https://storage.example/morning.png",
                ),
            ),
            (
                evening,
                LinkedMedia(
                    name="duplicate.png",
                    mime_type="image/png",
                    path="shared/image.png",
                    source_revision="sha256:shared",
                    url="https://storage.example/duplicate.png",
                ),
            ),
            (
                evening,
                LinkedMedia(
                    name="evening.png",
                    mime_type="image/png",
                    path="evening/image.png",
                    source_revision="sha256:evening",
                    url="https://storage.example/evening.png",
                ),
            ),
        ]
        return InlineImagesPage(special_image=None, ordinary_images=tuple(images), next_cursor=next_cursor)

    monkeypatch.setattr(inline, "get_inline_images", get_inline_images)

    page = await inline._get_inline_results([morning, evening], cursor=expected_cursor)
    payloads = [result.model_dump(exclude_none=True) for result in page.results]

    assert_that([payload["title"] for payload in payloads], equal_to(["shared.png", "morning.png", "evening.png"]))
    assert_that(payloads[0]["description"], equal_to("Категория /morning"))
    assert_that(payloads[2]["description"], equal_to("Категория /evening"))
    assert_that({payload["id"] for payload in payloads}, has_length(3))
    assert_that(page.next_cursor, equal_to(next_cursor))


def test_build_inline_category_results_preserves_media_types_and_namespaces_category_ids() -> None:
    categories = [
        _subscription_type(1, "/morning", "morning"),
        _subscription_type(2, "/day", "day"),
        _subscription_type(3, "/evening", "evening"),
        _subscription_type(4, "/night", "night"),
    ]
    images: list[tuple[SubscriptionType, CachedMedia | LinkedMedia]] = [
        (
            categories[0],
            CachedMedia(
                name="cached-photo.png",
                mime_type="image/png",
                path="shared/image.png",
                source_revision="sha256:shared",
                id="telegram-photo",
            ),
        ),
        (
            categories[1],
            LinkedMedia(
                name="linked-photo.png",
                mime_type="image/png",
                path="shared/image.png",
                source_revision="sha256:shared",
                url="https://storage.example/photo.png",
            ),
        ),
        (
            categories[2],
            CachedMedia(
                name="cached-animation.gif",
                mime_type="image/gif",
                path="evening/animation.gif",
                source_revision="sha256:cached-animation",
                id="telegram-animation",
            ),
        ),
        (
            categories[3],
            LinkedMedia(
                name="linked-animation.gif",
                mime_type="image/gif",
                path="night/animation.gif",
                source_revision="sha256:linked-animation",
                url="https://storage.example/animation.gif",
            ),
        ),
    ]

    results = inline._build_inline_category_results(images)
    payloads = [result.model_dump(exclude_none=True) for result in results]

    assert_that(
        [type(result) for result in results],
        equal_to(
            [
                InlineQueryResultCachedPhoto,
                InlineQueryResultPhoto,
                InlineQueryResultCachedGif,
                InlineQueryResultGif,
            ]
        ),
    )
    assert_that(
        [payload["title"] for payload in payloads],
        equal_to([f"🎲 {keyboards.format_category_button_text(category)}" for category in categories]),
    )
    assert_that(
        [
            payloads[0]["photo_file_id"],
            payloads[1]["photo_url"],
            payloads[2]["gif_file_id"],
            payloads[3]["gif_url"],
        ],
        equal_to(
            [
                "telegram-photo",
                "https://storage.example/photo.png",
                "telegram-animation",
                "https://storage.example/animation.gif",
            ]
        ),
    )
    result_ids = [result.id for result in results]
    assert_that([len(result_id) for result_id in result_ids], equal_to([64, 64, 64, 64]))
    assert_that(len(set(result_ids)), equal_to(4))
    assert_that(result_ids[0] == result_ids[1], equal_to(False))
    assert_that(
        set(result_ids).isdisjoint(
            inline._inline_result(image, subscription_type=category).id for category, image in images
        ),
        equal_to(True),
    )


@pytest.mark.parametrize(
    ("image", "expected_type"),
    [
        (
            CachedMedia(
                name="cached-photo.png",
                mime_type="image/png",
                path="cached-photo.png",
                source_revision="sha256:cached-photo",
                id="telegram-photo",
            ),
            InlineQueryResultCachedPhoto,
        ),
        (
            CachedMedia(
                name="cached-animation.gif",
                mime_type="image/gif",
                path="cached-animation.gif",
                source_revision="sha256:cached-animation",
                id="telegram-animation",
            ),
            InlineQueryResultCachedGif,
        ),
        (
            LinkedMedia(
                name="linked-photo.png",
                mime_type="image/png",
                path="linked-photo.png",
                source_revision="sha256:linked-photo",
                url="https://storage.example/photo.png",
            ),
            InlineQueryResultPhoto,
        ),
        (
            LinkedMedia(
                name="linked-animation.gif",
                mime_type="image/gif",
                path="linked-animation.gif",
                source_revision="sha256:linked-animation",
                url="https://storage.example/animation.gif",
            ),
            InlineQueryResultGif,
        ),
    ],
)
def test_build_random_inline_result_preserves_selected_media_type_and_source(
    image: CachedMedia | LinkedMedia,
    expected_type: type[inline.InlineMediaResult],
) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    ordinary_result = inline._inline_result(image, subscription_type=subscription_type)

    random_result = inline._build_random_inline_result(subscription_type, image)

    assert type(random_result) is expected_type
    assert_that(
        random_result.model_dump(exclude={"id", "title"}, exclude_none=True),
        equal_to(ordinary_result.model_dump(exclude={"id", "title"}, exclude_none=True)),
    )
    assert_that(random_result.title, equal_to(inline.RANDOM_INLINE_RESULT_TITLE))
    assert_that(random_result.id, has_length(64))
    assert_that(random_result.id, not_(equal_to(ordinary_result.id)))


@pytest.mark.parametrize("query_text", ["", "   ", " / "])
async def test_answer_inline_query_uses_category_results_for_normalized_empty_query(
    monkeypatch: MonkeyPatch,
    query_text: str,
) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    category_results = _inline_results(2)
    query = _FakeInlineQuery(query=query_text)

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        assert_that(query, equal_to(query_text))
        return [subscription_type]

    async def get_inline_category_results(
        subscription_types: list[SubscriptionType],
    ) -> list[inline.InlineMediaResult]:
        assert_that(subscription_types, equal_to([subscription_type]))
        return category_results

    async def fail_get_inline_results(
        subscription_types: list[SubscriptionType],
        *,
        cursor: InlinePaginationCursor | None,
    ) -> inline.InlineResultsPage:
        raise AssertionError("ordinary inline results must not be loaded for an empty query")

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)
    monkeypatch.setattr(inline, "_get_inline_category_results", get_inline_category_results)
    monkeypatch.setattr(inline, "_get_inline_results", fail_get_inline_results)

    await inline.answer_inline_query(cast(InlineQuery, query))

    assert_that(query.results, equal_to(category_results))
    assert_that(query.cache_time, equal_to(0))
    assert_that(query.is_personal, equal_to(True))
    assert_that(query.next_offset, equal_to(""))


async def test_answer_inline_query_passes_offset_and_next_offset_and_disables_telegram_cache(
    monkeypatch: MonkeyPatch,
) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    results = _inline_results(inline.MAX_INLINE_QUERY_RESULTS)
    incoming_cursor = InlinePaginationCursor(seed=b"seed-001", offset=49)
    next_cursor = InlinePaginationCursor(seed=b"seed-001", offset=99)
    query = _FakeInlineQuery(
        query="  DAY ",
        offset=encode_inline_pagination_cursor(incoming_cursor, "day"),
    )

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        assert_that(query, equal_to("  DAY "))
        return [subscription_type]

    async def get_inline_results(
        subscription_types: list[SubscriptionType],
        *,
        cursor: InlinePaginationCursor | None,
    ) -> inline.InlineResultsPage:
        assert_that(subscription_types, equal_to([subscription_type]))
        assert_that(cursor, equal_to(incoming_cursor))
        return inline.InlineResultsPage(results=tuple(results), next_cursor=next_cursor)

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)
    monkeypatch.setattr(inline, "_get_inline_results", get_inline_results)

    await inline.answer_inline_query(cast(InlineQuery, query))

    assert_that(query.results, equal_to(results))
    assert_that(query.cache_time, equal_to(0))
    assert_that(query.is_personal, equal_to(True))
    assert_that(query.next_offset, equal_to(encode_inline_pagination_cursor(next_cursor, "day")))


async def test_answer_inline_query_rejects_invalid_cursor_with_empty_terminal_page(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    query = _FakeInlineQuery(query="day", offset="invalid")

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        return [subscription_type]

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)

    with caplog.at_level(logging.INFO):
        await inline.answer_inline_query(cast(InlineQuery, query))

    assert_that(query.results, empty())
    assert_that(query.next_offset, equal_to(""))
    assert "Rejected inline pagination cursor" in caplog.text
    event = _last_inline_metrics_event(caplog)
    assert_that(event["outcome"], equal_to("handled_error"))


async def test_answer_inline_query_records_handled_result_error(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    query = _FakeInlineQuery(query="day")

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        return [subscription_type]

    async def get_inline_results(
        subscription_types: list[SubscriptionType],
        *,
        cursor: InlinePaginationCursor | None,
    ) -> inline.InlineResultsPage:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)
    monkeypatch.setattr(inline, "_get_inline_results", get_inline_results)

    with caplog.at_level(logging.INFO):
        await inline.answer_inline_query(cast(InlineQuery, query))

    assert_that(query.results, empty())
    event = _last_inline_metrics_event(caplog)
    assert_that(event["outcome"], equal_to("handled_error"))
    assert_that(event["counts"]["telegram_calls"], equal_to(1))


async def test_answer_inline_query_records_and_propagates_unhandled_error(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    query = _FakeInlineQuery(query="day")

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="database unavailable"):
        await inline.answer_inline_query(cast(InlineQuery, query))

    event = _last_inline_metrics_event(caplog)
    assert_that(event["outcome"], equal_to("unhandled_error"))
    assert_that(event["counts"]["telegram_calls"], equal_to(0))


async def test_answer_inline_query_records_and_propagates_cancellation(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    query = _FakeInlineQuery(query="day")

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        raise asyncio.CancelledError

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)

    with caplog.at_level(logging.INFO), pytest.raises(asyncio.CancelledError):
        await inline.answer_inline_query(cast(InlineQuery, query))

    event = _last_inline_metrics_event(caplog)
    assert_that(event["outcome"], equal_to("cancelled"))
    assert_that(event["counts"]["telegram_calls"], equal_to(0))


async def _async_result[T](value: T) -> T:
    return value


def _inline_results(count: int) -> list[inline.InlineMediaResult]:
    return [
        InlineQueryResultPhoto(
            id=f"result-{index}",
            photo_url=f"https://storage.example/{index}.png",
            thumbnail_url=f"https://storage.example/{index}.png",
        )
        for index in range(count)
    ]


def _subscription_type(
    subscription_type_id: int,
    name: str,
    directory: str,
    *,
    search_aliases: tuple[str, ...] = (),
) -> SubscriptionType:
    now = datetime.now(UTC)
    return SubscriptionType(
        id=subscription_type_id,
        name=name,
        time=time(13, 0, tzinfo=UTC),
        s3_directory_path=directory,
        search_aliases=search_aliases,
        created_at=now,
        updated_at=now,
    )


class _FakeInlineQuery:
    def __init__(self, *, query: str, offset: str = "") -> None:
        self.query = query
        self.offset = offset
        self.from_user = _FakeUser()
        self.results: list[InlineQueryResultUnion] = []
        self.cache_time: int | None = None
        self.is_personal: bool | None = None
        self.next_offset: str | None = None

    async def answer(
        self,
        results: list[InlineQueryResultUnion],
        *,
        cache_time: int,
        is_personal: bool,
        next_offset: str,
    ) -> None:
        self.results = results
        self.cache_time = cache_time
        self.is_personal = is_personal
        self.next_offset = next_offset


class _FakeUser:
    id = 42


def _last_inline_metrics_event(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    return next(
        json.loads(message)
        for message in reversed(caplog.messages)
        if message.startswith("{") and '"event":"inline_query_metrics"' in message
    )

import asyncio
import json
import logging
from collections.abc import Sequence
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
from hamcrest import assert_that, empty, equal_to, has_length, is_, not_, same_instance
from pytest import MonkeyPatch

from cringe_pics_telebot.bot import inline, keyboards
from cringe_pics_telebot.repositories.postgres import SubscriptionType
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

    async def get_inline_images(
        subscription_types: list[SubscriptionType],
    ) -> list[tuple[SubscriptionType, CachedMedia | LinkedMedia]]:
        assert_that(subscription_types, equal_to([morning, evening]))
        return [
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

    monkeypatch.setattr(inline, "get_inline_images", get_inline_images)

    results = await inline._get_inline_results([morning, evening])
    payloads = [result.model_dump(exclude_none=True) for result in results]

    assert_that([payload["title"] for payload in payloads], equal_to(["shared.png", "morning.png", "evening.png"]))
    assert_that(payloads[0]["description"], equal_to("Категория /morning"))
    assert_that(payloads[2]["description"], equal_to("Категория /evening"))
    assert_that({payload["id"] for payload in payloads}, has_length(3))


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


def test_shuffle_inline_results_uses_injected_shuffler_on_a_copy() -> None:
    results = _inline_results(3)
    shuffled_inputs: list[list[str]] = []

    def reverse(items: list[inline.InlineMediaResult]) -> None:
        shuffled_inputs.append([item.id for item in items])
        items.reverse()

    shuffled_results = inline._shuffle_inline_results(results, shuffler=reverse)

    assert_that(shuffled_inputs, equal_to([["result-0", "result-1", "result-2"]]))
    assert_that([result.id for result in shuffled_results], equal_to(["result-2", "result-1", "result-0"]))
    assert_that([result.id for result in results], equal_to(["result-0", "result-1", "result-2"]))


def test_prepare_inline_results_chooses_before_limit_and_deduplicates_selected_media() -> None:
    results = _inline_results(inline.MAX_INLINE_QUERY_RESULTS + 2)
    chooser_inputs: list[list[str]] = []
    shuffler_inputs: list[list[str]] = []

    def choose_last(items: Sequence[inline.InlineMediaResult]) -> inline.InlineMediaResult:
        chooser_inputs.append([item.id for item in items])
        return items[-1]

    def reverse(items: list[inline.InlineMediaResult]) -> None:
        shuffler_inputs.append([item.id for item in items])
        items.reverse()

    prepared_results = inline._prepare_inline_results(
        results,
        chooser=choose_last,
        shuffler=reverse,
    )
    random_result = cast(InlineQueryResultPhoto, prepared_results[0])

    assert_that(
        chooser_inputs,
        equal_to([[f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 2)]]),
    )
    assert_that(
        shuffler_inputs,
        equal_to([[f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 1)]]),
    )
    assert_that(prepared_results, has_length(inline.MAX_INLINE_QUERY_RESULTS))
    assert_that(random_result.title, equal_to(inline.RANDOM_INLINE_RESULT_TITLE))
    assert_that(random_result.id, has_length(64))
    assert_that(random_result.id in tuple(result.id for result in results), is_(False))
    assert_that(random_result.photo_url, equal_to("https://storage.example/51.png"))
    assert_that(
        [result.id for result in prepared_results[1:]],
        equal_to([f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS, 1, -1)]),
    )
    assert_that(
        [result.id for result in results],
        equal_to([f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 2)]),
    )


def test_prepare_inline_results_prefers_cached_for_random_and_keeps_status_groups() -> None:
    results: list[inline.InlineMediaResult] = [
        InlineQueryResultPhoto(
            id="linked-1",
            photo_url="https://storage.example/linked-1.png",
            thumbnail_url="https://storage.example/linked-1.png",
        ),
        InlineQueryResultCachedPhoto(id="cached-1", photo_file_id="telegram-1"),
        InlineQueryResultPhoto(
            id="linked-2",
            photo_url="https://storage.example/linked-2.png",
            thumbnail_url="https://storage.example/linked-2.png",
        ),
        InlineQueryResultCachedPhoto(id="cached-2", photo_file_id="telegram-2"),
    ]
    chooser_inputs: list[list[str]] = []
    shuffler_inputs: list[list[str]] = []

    def choose_last(items: Sequence[inline.InlineMediaResult]) -> inline.InlineMediaResult:
        chooser_inputs.append([item.id for item in items])
        return items[-1]

    def reverse(items: list[inline.InlineMediaResult]) -> None:
        shuffler_inputs.append([item.id for item in items])
        items.reverse()

    prepared = inline._prepare_inline_results(
        results,
        chooser=choose_last,
        shuffler=reverse,
    )
    random_result = cast(InlineQueryResultCachedPhoto, prepared[0])

    assert_that(chooser_inputs, equal_to([["cached-1", "cached-2"]]))
    assert_that(shuffler_inputs, equal_to([["cached-1"], ["linked-1", "linked-2"]]))
    assert_that(random_result.photo_file_id, equal_to("telegram-2"))
    assert_that(random_result.title, equal_to(inline.RANDOM_INLINE_RESULT_TITLE))
    assert_that([result.id for result in prepared[1:]], equal_to(["cached-1", "linked-2", "linked-1"]))


def test_prepare_inline_results_returns_empty_results() -> None:
    assert_that(inline._prepare_inline_results([]), empty())


@pytest.mark.parametrize(
    "result",
    [
        InlineQueryResultCachedPhoto(id="cached-photo", photo_file_id="telegram-photo"),
        InlineQueryResultCachedGif(id="cached-gif", gif_file_id="telegram-gif"),
        InlineQueryResultPhoto(
            id="linked-photo",
            photo_url="https://storage.example/photo.png",
            thumbnail_url="https://storage.example/photo.png",
        ),
        InlineQueryResultGif(
            id="linked-gif",
            gif_url="https://storage.example/animation.gif",
            thumbnail_url="https://storage.example/animation.gif",
        ),
    ],
)
def test_prepare_inline_results_preserves_selected_media_type_and_source(result: inline.InlineMediaResult) -> None:
    prepared_result = inline._prepare_inline_results([result])[0]

    assert_that(type(prepared_result), same_instance(type(result)))
    assert_that(
        prepared_result.model_dump(exclude={"id", "title"}, exclude_none=True),
        equal_to(result.model_dump(exclude={"id", "title"}, exclude_none=True)),
    )
    assert_that(prepared_result.title, equal_to(inline.RANDOM_INLINE_RESULT_TITLE))
    assert_that(prepared_result.id, not_(equal_to(result.id)))


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

    async def fail_get_inline_results(subscription_types: list[SubscriptionType]) -> list[inline.InlineMediaResult]:
        raise AssertionError("ordinary inline results must not be loaded for an empty query")

    def fail_prepare_inline_results(results: list[inline.InlineMediaResult]) -> list[inline.InlineMediaResult]:
        raise AssertionError("category results must not receive the ordinary random result")

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)
    monkeypatch.setattr(inline, "_get_inline_category_results", get_inline_category_results)
    monkeypatch.setattr(inline, "_get_inline_results", fail_get_inline_results)
    monkeypatch.setattr(inline, "_prepare_inline_results", fail_prepare_inline_results)

    await inline.answer_inline_query(cast(InlineQuery, query))

    assert_that(query.results, equal_to(category_results))
    assert_that(query.cache_time, equal_to(0))
    assert_that(query.is_personal, equal_to(True))


async def test_answer_inline_query_prepares_results_and_disables_telegram_cache(monkeypatch: MonkeyPatch) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    results = _inline_results(inline.MAX_INLINE_QUERY_RESULTS + 1)
    query = _FakeInlineQuery(query="day")

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        assert_that(query, equal_to("day"))
        return [subscription_type]

    async def get_inline_results(subscription_types: list[SubscriptionType]) -> list[inline.InlineMediaResult]:
        assert_that(subscription_types, equal_to([subscription_type]))
        return results

    def reverse(items: list[inline.InlineMediaResult]) -> None:
        items.reverse()

    def choose_last(items: Sequence[inline.InlineMediaResult]) -> inline.InlineMediaResult:
        return items[-1]

    monkeypatch.setattr(inline, "_find_subscription_types", find_subscription_types)
    monkeypatch.setattr(inline, "_get_inline_results", get_inline_results)
    monkeypatch.setattr(inline.random, "shuffle", reverse)
    monkeypatch.setattr(inline.random, "choice", choose_last)

    await inline.answer_inline_query(cast(InlineQuery, query))

    assert_that(query.results, has_length(inline.MAX_INLINE_QUERY_RESULTS))
    assert_that(query.results[0].id, has_length(64))
    assert_that(query.results[0].id in tuple(result.id for result in results), is_(False))
    assert_that(
        cast(inline.InlineMediaResult, query.results[0]).title,
        equal_to(inline.RANDOM_INLINE_RESULT_TITLE),
    )
    assert_that(
        [result.id for result in query.results[1:]],
        equal_to([f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS - 1, 0, -1)]),
    )
    assert_that(query.cache_time, equal_to(0))
    assert_that(query.is_personal is True, is_(True))
    assert_that(
        [result.id for result in results],
        equal_to([f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 1)]),
    )


async def test_answer_inline_query_records_handled_result_error(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    query = _FakeInlineQuery(query="day")

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        return [subscription_type]

    async def get_inline_results(subscription_types: list[SubscriptionType]) -> list[inline.InlineMediaResult]:
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
    def __init__(self, *, query: str) -> None:
        self.query = query
        self.from_user = _FakeUser()
        self.results: list[InlineQueryResultUnion] = []
        self.cache_time: int | None = None
        self.is_personal: bool | None = None

    async def answer(
        self,
        results: list[InlineQueryResultUnion],
        *,
        cache_time: int,
        is_personal: bool,
    ) -> None:
        self.results = results
        self.cache_time = cache_time
        self.is_personal = is_personal


class _FakeUser:
    id = 42


def _last_inline_metrics_event(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    return next(
        json.loads(message)
        for message in reversed(caplog.messages)
        if message.startswith("{") and '"event":"inline_query_metrics"' in message
    )

from collections.abc import Sequence
from datetime import UTC, datetime, time
from typing import cast

import pytest
from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultUnion,
)
from pytest import MonkeyPatch

from cringe_pics_telebot.bot import inline
from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.services.inline_images import CachedInlineImage, LinkedInlineImage


@pytest.mark.parametrize(
    ("query", "category", "matches"),
    [
        ("day", "/day", True),
        ("  DA ", "/day", True),
        ("/d", "/day", True),
        ("ING", "/evening", True),
        ("", "/day", False),
        (" / ", "/day", False),
        ("night", "/day", False),
    ],
)
def test_category_matches_query(query: str, category: str, matches: bool) -> None:
    assert inline.category_matches_query(query, category) is matches


async def test_find_subscription_types_returns_every_matching_category(monkeypatch: MonkeyPatch) -> None:
    subscription_types = [
        _subscription_type(1, "/morning", "morning"),
        _subscription_type(2, "/day", "day"),
        _subscription_type(3, "/evening", "evening"),
        _subscription_type(4, "/night", "night"),
    ]

    async def get_subscription_types() -> list[SubscriptionType]:
        return subscription_types

    monkeypatch.setattr(inline, "get_subscription_types", get_subscription_types)

    assert await inline._find_subscription_types("  ING ") == [subscription_types[0], subscription_types[2]]


async def test_get_inline_results_combines_categories_without_duplicate_paths(monkeypatch: MonkeyPatch) -> None:
    morning = _subscription_type(1, "/morning", "morning")
    evening = _subscription_type(3, "/evening", "evening")

    async def get_inline_images(
        subscription_type: SubscriptionType,
        *,
        limit: int | None,
    ) -> list[CachedInlineImage | LinkedInlineImage]:
        assert limit is None
        if subscription_type == morning:
            return [
                CachedInlineImage(
                    name="shared.png",
                    mime_type="image/png",
                    path="shared/image.png",
                    file_id="telegram-shared",
                ),
                LinkedInlineImage(
                    name="morning.png",
                    mime_type="image/png",
                    path="morning/image.png",
                    url="https://storage.example/morning.png",
                ),
            ]

        return [
            LinkedInlineImage(
                name="duplicate.png",
                mime_type="image/png",
                path="shared/image.png",
                url="https://storage.example/duplicate.png",
            ),
            LinkedInlineImage(
                name="evening.png",
                mime_type="image/png",
                path="evening/image.png",
                url="https://storage.example/evening.png",
            ),
        ]

    monkeypatch.setattr(inline, "get_inline_images", get_inline_images)

    results = await inline._get_inline_results([morning, evening])
    payloads = [result.model_dump(exclude_none=True) for result in results]

    assert [payload["title"] for payload in payloads] == ["shared.png", "morning.png", "evening.png"]
    assert payloads[0]["description"] == "Категория /morning"
    assert payloads[2]["description"] == "Категория /evening"
    assert len({payload["id"] for payload in payloads}) == 3


def test_shuffle_inline_results_uses_injected_shuffler_on_a_copy() -> None:
    results = _inline_results(3)
    shuffled_inputs: list[list[str]] = []

    def reverse(items: list[inline.InlineMediaResult]) -> None:
        shuffled_inputs.append([item.id for item in items])
        items.reverse()

    shuffled_results = inline._shuffle_inline_results(results, shuffler=reverse)

    assert shuffled_inputs == [["result-0", "result-1", "result-2"]]
    assert [result.id for result in shuffled_results] == ["result-2", "result-1", "result-0"]
    assert [result.id for result in results] == ["result-0", "result-1", "result-2"]


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

    assert chooser_inputs == [[f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 2)]]
    assert shuffler_inputs == [[f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 1)]]
    assert len(prepared_results) == inline.MAX_INLINE_QUERY_RESULTS
    assert random_result.title == inline.RANDOM_INLINE_RESULT_TITLE
    assert len(random_result.id) == 64
    assert random_result.id not in {result.id for result in results}
    assert random_result.photo_url == "https://storage.example/51.png"
    assert [result.id for result in prepared_results[1:]] == [
        f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS, 1, -1)
    ]
    assert [result.id for result in results] == [
        f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 2)
    ]


def test_prepare_inline_results_returns_empty_results() -> None:
    assert inline._prepare_inline_results([]) == []


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

    assert type(prepared_result) is type(result)
    assert prepared_result.model_dump(exclude={"id", "title"}, exclude_none=True) == result.model_dump(
        exclude={"id", "title"},
        exclude_none=True,
    )
    assert prepared_result.title == inline.RANDOM_INLINE_RESULT_TITLE
    assert prepared_result.id != result.id


async def test_answer_inline_query_prepares_results_and_disables_telegram_cache(monkeypatch: MonkeyPatch) -> None:
    subscription_type = _subscription_type(2, "/day", "day")
    results = _inline_results(inline.MAX_INLINE_QUERY_RESULTS + 1)
    query = _FakeInlineQuery(query="day")

    async def find_subscription_types(query: str) -> list[SubscriptionType]:
        assert query == "day"
        return [subscription_type]

    async def get_inline_results(subscription_types: list[SubscriptionType]) -> list[inline.InlineMediaResult]:
        assert subscription_types == [subscription_type]
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

    assert len(query.results) == inline.MAX_INLINE_QUERY_RESULTS
    assert len(query.results[0].id) == 64
    assert query.results[0].id not in {result.id for result in results}
    assert cast(inline.InlineMediaResult, query.results[0]).title == inline.RANDOM_INLINE_RESULT_TITLE
    assert [result.id for result in query.results[1:]] == [
        f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS - 1, 0, -1)
    ]
    assert query.cache_time == 0
    assert query.is_personal is True
    assert [result.id for result in results] == [
        f"result-{index}" for index in range(inline.MAX_INLINE_QUERY_RESULTS + 1)
    ]


def _inline_results(count: int) -> list[inline.InlineMediaResult]:
    return [
        InlineQueryResultPhoto(
            id=f"result-{index}",
            photo_url=f"https://storage.example/{index}.png",
            thumbnail_url=f"https://storage.example/{index}.png",
        )
        for index in range(count)
    ]


def _subscription_type(subscription_type_id: int, name: str, directory: str) -> SubscriptionType:
    now = datetime.now(UTC)
    return SubscriptionType(
        id=subscription_type_id,
        name=name,
        time=time(13, 0, tzinfo=UTC),
        s3_directory_path=directory,
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

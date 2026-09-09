from datetime import UTC, datetime, time

import pytest
from hamcrest import assert_that, equal_to, same_instance

from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.services.inline_search import (
    InlineSearchMode,
    category_matches_query,
    resolve_inline_search,
)


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
    assert_that(category_matches_query(query, category, search_aliases), equal_to(matches))


@pytest.mark.parametrize("query", ["", "  ", " / ", "\t/\n"])
def test_resolve_inline_search_preserves_empty_category_picker(query: str) -> None:
    categories = (_category(1, "/day"), _category(2, "/evening"))

    search = resolve_inline_search(query, categories)

    assert_that(search.mode, same_instance(InlineSearchMode.empty))
    assert_that(search.subscription_types, equal_to(categories))
    assert_that(search.search_terms, equal_to(()))


def test_single_token_keeps_partial_multi_category_search() -> None:
    categories = (
        _category(1, "/morning", aliases=("общее",)),
        _category(2, "/day"),
        _category(3, "/evening", aliases=("ОБЩЕЕ",)),
    )

    search = resolve_inline_search(" /ОБЩ ", categories)

    assert_that(search.mode, same_instance(InlineSearchMode.category))
    assert_that(search.subscription_types, equal_to((categories[0], categories[2])))
    assert_that(search.search_terms, equal_to(()))


def test_single_unknown_category_token_becomes_global_media_search() -> None:
    categories = (_category(1, "/day"), _category(2, "/evening"))

    search = resolve_inline_search("  КОТ  ", categories)

    assert_that(search.mode, same_instance(InlineSearchMode.global_media))
    assert_that(search.subscription_types, equal_to(categories))
    assert_that(search.search_terms, equal_to(("кот",)))


def test_exact_category_name_scopes_remaining_media_terms() -> None:
    day = _category(1, "/day", aliases=("день",))
    evening = _category(2, "/evening")

    search = resolve_inline_search(" /DAY   Сонный\tКОТ ", (day, evening))

    assert_that(search.mode, same_instance(InlineSearchMode.category_media))
    assert_that(search.subscription_types, equal_to((day,)))
    assert_that(search.search_terms, equal_to(("сонный", "кот")))


def test_unique_one_word_category_alias_scopes_media_search() -> None:
    day = _category(1, "/day", aliases=("день", "дневная категория"))
    evening = _category(2, "/evening", aliases=("вечер",))

    search = resolve_inline_search("день кот", (day, evening))

    assert_that(search.mode, same_instance(InlineSearchMode.category_media))
    assert_that(search.subscription_types, equal_to((day,)))
    assert_that(search.search_terms, equal_to(("кот",)))


def test_ambiguous_category_alias_keeps_full_query_global() -> None:
    categories = (
        _category(1, "/morning", aliases=("общее",)),
        _category(2, "/evening", aliases=("ОБЩЕЕ",)),
    )

    search = resolve_inline_search("общее кот", categories)

    assert_that(search.mode, same_instance(InlineSearchMode.global_media))
    assert_that(search.subscription_types, equal_to(categories))
    assert_that(search.search_terms, equal_to(("общее", "кот")))


def test_exact_name_takes_priority_over_same_category_alias() -> None:
    day = _category(1, "/day")
    other = _category(2, "/other", aliases=("day",))

    search = resolve_inline_search("day кот", (day, other))

    assert_that(search.mode, same_instance(InlineSearchMode.category_media))
    assert_that(search.subscription_types, equal_to((day,)))


def _category(
    category_id: int,
    name: str,
    *,
    aliases: tuple[str, ...] = (),
) -> SubscriptionType:
    now = datetime.now(UTC)
    return SubscriptionType(
        id=category_id,
        name=name,
        time=time(13),
        s3_directory_path=name.removeprefix("/"),
        search_aliases=aliases,
        is_active=True,
        created_at=now,
        updated_at=now,
    )

import pytest
from hamcrest import assert_that, equal_to

from cringe_pics_telebot.entities.search_aliases import SearchAlias
from cringe_pics_telebot.services.media_search_aliases import (
    InvalidMediaSearchAliasesError,
    parse_media_search_aliases,
)
from cringe_pics_telebot.services.search_aliases import (
    InvalidSearchAliasesError,
    normalize_search_term,
    parse_search_aliases,
)


@pytest.mark.parametrize(
    ("value", "strip_leading_slash", "normalized"),
    [
        ("  КОТ\t спит  ", False, "кот спит"),
        (" /ДЕНЬ ", True, "день"),
        (" / кот ", True, "кот"),
        ("Straße", False, "strasse"),
    ],
)
def test_normalize_search_term(
    value: str,
    strip_leading_slash: bool,
    normalized: str,
) -> None:
    assert_that(
        normalize_search_term(value, strip_leading_slash=strip_leading_slash),
        equal_to(normalized),
    )


def test_parse_search_aliases_preserves_first_spelling_and_collapses_normalized_duplicates() -> None:
    aliases = parse_search_aliases("  Сонный   кот  \nСОННЫЙ КОТ\n\n хочет спать \n")

    assert_that(
        aliases,
        equal_to(
            (
                SearchAlias(text="Сонный   кот", normalized="сонный кот"),
                SearchAlias(text="хочет спать", normalized="хочет спать"),
            )
        ),
    )


@pytest.mark.parametrize("value", ["", "  \n\t"])
def test_parse_search_aliases_rejects_empty_input(value: str) -> None:
    with pytest.raises(InvalidSearchAliasesError):
        parse_search_aliases(value)


def test_parse_media_search_aliases_uses_domain_specific_error() -> None:
    with pytest.raises(InvalidMediaSearchAliasesError):
        parse_media_search_aliases(" \n ")

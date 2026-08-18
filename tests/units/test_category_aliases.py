import pytest

from cringe_pics_telebot.services.category_aliases import (
    InvalidCategoryAliasesError,
    parse_category_search_aliases,
)


def test_parse_category_search_aliases_preserves_first_unique_spelling() -> None:
    assert parse_category_search_aliases("  полдень  \n/ДЕНЬ\nдень\n\n с обеда \n / \nПОЛДЕНЬ") == (
        "полдень",
        "/ДЕНЬ",
        "с обеда",
    )


@pytest.mark.parametrize("value", ["", "  \n\t", " / \n / "])
def test_parse_category_search_aliases_rejects_no_searchable_aliases(value: str) -> None:
    with pytest.raises(InvalidCategoryAliasesError):
        parse_category_search_aliases(value)

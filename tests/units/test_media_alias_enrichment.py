from datetime import timedelta

import pytest
from hamcrest import assert_that, contains_exactly, has_properties

from cringe_pics_telebot.repositories.ollama import InvalidOllamaResponseError
from cringe_pics_telebot.services.media_alias_enrichment import media_alias_retry_delay, parse_generated_media_aliases


def test_generated_aliases_normalize_and_deduplicate_without_splitting_one_alias_into_many() -> None:
    aliases = parse_generated_media_aliases(("  Сонный кот  ", "СОННЫЙ   КОТ", "С кофе\nв лапах", "  "))
    assert_that(
        aliases,
        contains_exactly(
            has_properties(text="Сонный кот", normalized="сонный кот"),
            has_properties(text="С кофе\nв лапах", normalized="с кофе в лапах"),
        ),
    )


@pytest.mark.parametrize("values", [(), (" ",), ("\ud800",), ("with\0null",), ("x" * 101,), ("x",) * 21])
def test_generated_aliases_reject_invalid_or_empty_output(values: tuple[str, ...]) -> None:
    with pytest.raises(InvalidOllamaResponseError):
        parse_generated_media_aliases(values)


@pytest.mark.parametrize(("attempt", "expected"), [(1, 30), (2, 60), (3, 120), (4, 150), (1_000_000, 150)])
def test_retry_delay_doubles_and_caps_without_unbounded_exponent(attempt: int, expected: int) -> None:
    assert media_alias_retry_delay(attempt, base=timedelta(seconds=30), maximum=timedelta(seconds=150)) == timedelta(
        seconds=expected
    )

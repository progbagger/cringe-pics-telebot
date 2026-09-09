from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from cringe_pics_telebot.repositories.postgres import SubscriptionType

from .category_aliases import normalize_category_search_term


class InlineSearchMode(StrEnum):
    empty = "empty"
    category = "category"
    global_media = "global_media"
    category_media = "category_media"


@dataclass(frozen=True, slots=True)
class InlineSearch:
    mode: InlineSearchMode
    normalized_query: str
    subscription_types: tuple[SubscriptionType, ...]
    search_terms: tuple[str, ...] = ()


def resolve_inline_search(query: str, subscription_types: Sequence[SubscriptionType]) -> InlineSearch:
    normalized_query = normalize_category_search_term(query)
    categories = tuple(subscription_types)
    if not normalized_query:
        return InlineSearch(
            mode=InlineSearchMode.empty,
            normalized_query=normalized_query,
            subscription_types=categories,
        )

    search_terms = tuple(normalized_query.split())
    if len(search_terms) == 1:
        matching_categories = tuple(
            category
            for category in categories
            if category_matches_query(
                normalized_query,
                category.name,
                category.search_aliases,
            )
        )
        if matching_categories:
            return InlineSearch(
                mode=InlineSearchMode.category,
                normalized_query=normalized_query,
                subscription_types=matching_categories,
            )
        return InlineSearch(
            mode=InlineSearchMode.global_media,
            normalized_query=normalized_query,
            subscription_types=categories,
            search_terms=search_terms,
        )

    if category := _find_category_prefix(search_terms[0], categories):
        return InlineSearch(
            mode=InlineSearchMode.category_media,
            normalized_query=normalized_query,
            subscription_types=(category,),
            search_terms=search_terms[1:],
        )
    return InlineSearch(
        mode=InlineSearchMode.global_media,
        normalized_query=normalized_query,
        subscription_types=categories,
        search_terms=search_terms,
    )


def category_matches_query(query: str, category: str, search_aliases: Sequence[str] = ()) -> bool:
    normalized_query = normalize_category_search_term(query)
    if not normalized_query:
        return False

    normalized_terms = {
        normalized_term
        for term in (category, *search_aliases)
        if (normalized_term := normalize_category_search_term(term))
    }
    return any(normalized_query in term for term in normalized_terms)


def _find_category_prefix(
    prefix: str,
    subscription_types: Sequence[SubscriptionType],
) -> SubscriptionType | None:
    name_matches = [
        category for category in subscription_types if normalize_category_search_term(category.name) == prefix
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if name_matches:
        return None

    alias_matches = [
        category
        for category in subscription_types
        if any(normalize_category_search_term(alias) == prefix for alias in category.search_aliases)
    ]
    return alias_matches[0] if len(alias_matches) == 1 else None

from .search_aliases import InvalidSearchAliasesError, normalize_search_term, parse_search_aliases


class InvalidCategoryAliasesError(ValueError): ...


def normalize_category_search_term(term: str) -> str:
    return normalize_search_term(term, strip_leading_slash=True)


def parse_category_search_aliases(value: str) -> tuple[str, ...]:
    try:
        aliases = parse_search_aliases(value, strip_leading_slash=True)
    except InvalidSearchAliasesError as error:
        raise InvalidCategoryAliasesError from error
    return tuple(alias.text for alias in aliases)

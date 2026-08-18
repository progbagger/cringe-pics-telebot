class InvalidCategoryAliasesError(ValueError): ...


def normalize_category_search_term(term: str) -> str:
    return term.strip().removeprefix("/").casefold()


def parse_category_search_aliases(value: str) -> tuple[str, ...]:
    aliases: list[str] = []
    normalized_aliases: set[str] = set()

    for line in value.splitlines():
        alias = line.strip()
        normalized_alias = normalize_category_search_term(alias)
        if not normalized_alias or normalized_alias in normalized_aliases:
            continue

        aliases.append(alias)
        normalized_aliases.add(normalized_alias)

    if not aliases:
        raise InvalidCategoryAliasesError

    return tuple(aliases)

from cringe_pics_telebot.entities.search_aliases import SearchAlias


class InvalidSearchAliasesError(ValueError): ...


def normalize_search_term(term: str, *, strip_leading_slash: bool = False) -> str:
    stripped = term.strip()
    if strip_leading_slash:
        stripped = stripped.removeprefix("/")
    return " ".join(stripped.split()).casefold()


def parse_search_aliases(value: str, *, strip_leading_slash: bool = False) -> tuple[SearchAlias, ...]:
    aliases: list[SearchAlias] = []
    normalized_aliases: set[str] = set()

    for line in value.splitlines():
        text = line.strip()
        normalized = normalize_search_term(text, strip_leading_slash=strip_leading_slash)
        if not normalized or normalized in normalized_aliases:
            continue

        aliases.append(SearchAlias(text=text, normalized=normalized))
        normalized_aliases.add(normalized)

    if not aliases:
        raise InvalidSearchAliasesError

    return tuple(aliases)

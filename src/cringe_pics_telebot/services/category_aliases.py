def normalize_category_search_term(term: str) -> str:
    return term.strip().removeprefix("/").casefold()

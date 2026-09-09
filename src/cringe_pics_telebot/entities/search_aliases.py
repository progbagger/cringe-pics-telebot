from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchAlias:
    text: str
    normalized: str

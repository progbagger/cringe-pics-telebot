from collections.abc import Sequence

from cringe_pics_telebot.entities.search_aliases import SearchAlias
from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaSearchMetadata,
    get_category_media,
    replace_media_search_aliases,
    transaction,
)

from .search_aliases import InvalidSearchAliasesError, parse_search_aliases


class InvalidMediaSearchAliasesError(ValueError): ...


def parse_media_search_aliases(value: str) -> tuple[SearchAlias, ...]:
    try:
        return parse_search_aliases(value)
    except InvalidSearchAliasesError as error:
        raise InvalidMediaSearchAliasesError from error


async def set_media_search_aliases(
    media_id: int,
    aliases: Sequence[SearchAlias],
) -> CategoryMediaSearchMetadata | None:
    async with transaction():
        media = await get_category_media(media_id, with_for_update=True)
        if media is None:
            return None
        await replace_media_search_aliases(media_id, aliases)
    return CategoryMediaSearchMetadata(
        media=media,
        search_aliases=tuple(alias.text for alias in aliases),
    )

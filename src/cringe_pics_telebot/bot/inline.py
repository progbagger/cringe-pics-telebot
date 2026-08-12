import asyncio
import hashlib
import logging

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultUnion,
)

from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.services.inline_images import (
    MAX_INLINE_QUERY_RESULTS,
    CachedInlineImage,
    LinkedInlineImage,
    get_inline_images,
)
from cringe_pics_telebot.services.subscriptions import get_subscription_types

logger = logging.getLogger(__name__)

router = Router(name="inline")


@router.inline_query()
async def answer_inline_query(inline_query: InlineQuery) -> None:
    subscription_types = await _find_subscription_types(inline_query.query)
    if not subscription_types:
        await inline_query.answer([], cache_time=0, is_personal=True)
        return

    try:
        results = await _get_inline_results(subscription_types)
    except Exception:
        logger.exception(
            "Failed to prepare inline results for user %d and categories %s",
            inline_query.from_user.id,
            ", ".join(subscription_type.name for subscription_type in subscription_types),
        )
        results = []

    await inline_query.answer(results[:MAX_INLINE_QUERY_RESULTS], cache_time=0, is_personal=True)


async def _find_subscription_types(query: str) -> list[SubscriptionType]:
    if not _normalize_category(query):
        return []

    return [
        subscription_type
        for subscription_type in await get_subscription_types()
        if category_matches_query(query, subscription_type.name)
    ]


def category_matches_query(query: str, category: str) -> bool:
    normalized_query = _normalize_category(query)
    return bool(normalized_query) and normalized_query in _normalize_category(category)


def _normalize_category(category: str) -> str:
    return category.strip().removeprefix("/").casefold()


async def _get_inline_results(subscription_types: list[SubscriptionType]) -> list[InlineQueryResultUnion]:
    images_by_subscription_type = await asyncio.gather(
        *(get_inline_images(subscription_type) for subscription_type in subscription_types)
    )
    seen_paths: set[str] = set()
    results: list[InlineQueryResultUnion] = []

    for subscription_type, images in zip(subscription_types, images_by_subscription_type, strict=True):
        for image in images:
            if image.path in seen_paths:
                continue

            seen_paths.add(image.path)
            results.append(_inline_result(image, subscription_type=subscription_type))

    return results


def _inline_result(
    image: CachedInlineImage | LinkedInlineImage,
    *,
    subscription_type: SubscriptionType,
) -> InlineQueryResultUnion:
    result_id = hashlib.sha256(image.path.encode()).hexdigest()
    title = image.name

    if isinstance(image, CachedInlineImage):
        if _is_animation(image):
            return InlineQueryResultCachedGif(id=result_id, gif_file_id=image.file_id, title=title)

        return InlineQueryResultCachedPhoto(
            id=result_id,
            photo_file_id=image.file_id,
            title=title,
            description=f"Категория {subscription_type.name}",
        )

    if _is_animation(image):
        return InlineQueryResultGif(
            id=result_id,
            gif_url=image.url,
            thumbnail_url=image.url,
            thumbnail_mime_type="image/gif",
            title=title,
        )

    return InlineQueryResultPhoto(
        id=result_id,
        photo_url=image.url,
        thumbnail_url=image.url,
        title=title,
        description=f"Категория {subscription_type.name}",
    )


def _is_animation(image: CachedInlineImage | LinkedInlineImage) -> bool:
    return "gif" in image.mime_type

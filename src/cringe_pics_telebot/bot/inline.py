import asyncio
import hashlib
import logging
import random
from collections.abc import Callable, Sequence

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
from cringe_pics_telebot.services.category_aliases import normalize_category_search_term
from cringe_pics_telebot.services.inline_images import (
    MAX_INLINE_QUERY_RESULTS,
    get_inline_images,
)
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia
from cringe_pics_telebot.services.subscriptions import get_subscription_types

logger = logging.getLogger(__name__)

RANDOM_INLINE_RESULT_TITLE = "🎲 Выбрать случайную картинку"

type InlineMediaResult = (
    InlineQueryResultCachedGif | InlineQueryResultCachedPhoto | InlineQueryResultGif | InlineQueryResultPhoto
)

router = Router(name="inline")


@router.inline_query()
async def answer_inline_query(inline_query: InlineQuery) -> None:
    subscription_types = await _find_subscription_types(inline_query.query)
    if not subscription_types:
        await inline_query.answer([], cache_time=0, is_personal=True)
        return

    try:
        results = _prepare_inline_results(await _get_inline_results(subscription_types))
    except Exception:
        logger.exception(
            "Failed to prepare inline results for user %d and categories %s",
            inline_query.from_user.id,
            ", ".join(subscription_type.name for subscription_type in subscription_types),
        )
        results = []

    answer_results: list[InlineQueryResultUnion] = [*results[:MAX_INLINE_QUERY_RESULTS]]
    await inline_query.answer(answer_results, cache_time=0, is_personal=True)


async def _find_subscription_types(query: str) -> list[SubscriptionType]:
    if not normalize_category_search_term(query):
        return []

    return [
        subscription_type
        for subscription_type in await get_subscription_types()
        if category_matches_query(
            query,
            subscription_type.name,
            subscription_type.search_aliases,
        )
    ]


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


async def _get_inline_results(subscription_types: list[SubscriptionType]) -> list[InlineMediaResult]:
    images_by_subscription_type = await asyncio.gather(
        *(get_inline_images(subscription_type, limit=None) for subscription_type in subscription_types)
    )
    seen_paths: set[str] = set()
    results: list[InlineMediaResult] = []

    for subscription_type, images in zip(subscription_types, images_by_subscription_type, strict=True):
        for image in images:
            if image.path in seen_paths:
                continue

            seen_paths.add(image.path)
            results.append(_inline_result(image, subscription_type=subscription_type))

    return results


def _prepare_inline_results(
    results: list[InlineMediaResult],
    *,
    chooser: Callable[[Sequence[InlineMediaResult]], InlineMediaResult] | None = None,
    shuffler: Callable[[list[InlineMediaResult]], None] | None = None,
) -> list[InlineMediaResult]:
    if not results:
        return []

    selected_result = (chooser or random.choice)(results)
    random_result = selected_result.model_copy(
        update={
            "id": _random_result_id(selected_result.id),
            "title": RANDOM_INLINE_RESULT_TITLE,
        }
    )
    ordinary_results = [result for result in results if result.id != selected_result.id]
    shuffled_results = _shuffle_inline_results(ordinary_results, shuffler=shuffler)

    return [random_result, *shuffled_results[: MAX_INLINE_QUERY_RESULTS - 1]]


def _random_result_id(result_id: str) -> str:
    return hashlib.sha256(f"random:{result_id}".encode()).hexdigest()


def _shuffle_inline_results(
    results: list[InlineMediaResult],
    *,
    shuffler: Callable[[list[InlineMediaResult]], None] | None = None,
) -> list[InlineMediaResult]:
    shuffled_results = results.copy()
    (shuffler or random.shuffle)(shuffled_results)
    return shuffled_results


def _inline_result(
    image: CachedMedia | LinkedMedia,
    *,
    subscription_type: SubscriptionType,
) -> InlineMediaResult:
    result_id = hashlib.sha256(image.path.encode()).hexdigest()
    title = image.name

    if isinstance(image, CachedMedia):
        if _is_animation(image):
            return InlineQueryResultCachedGif(id=result_id, gif_file_id=image.id, title=title)

        return InlineQueryResultCachedPhoto(
            id=result_id,
            photo_file_id=image.id,
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


def _is_animation(image: CachedMedia | LinkedMedia) -> bool:
    return "gif" in image.mime_type

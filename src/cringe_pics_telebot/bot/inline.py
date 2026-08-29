import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass

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
    get_inline_category_images,
    get_inline_images,
)
from cringe_pics_telebot.services.inline_metrics import (
    CATEGORIES_LOOKUP_STAGE,
    RESULTS_PREPARE_STAGE,
    TELEGRAM_ANSWER_STAGE,
    InlineQueryMetrics,
    get_inline_query_metrics,
    inline_query_stage,
)
from cringe_pics_telebot.services.inline_pagination import (
    InlinePaginationCursor,
    InvalidInlinePaginationCursor,
    decode_inline_pagination_cursor,
    encode_inline_pagination_cursor,
)
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia
from cringe_pics_telebot.services.subscriptions import get_scheduled_subscription_types

from .keyboards import format_category_button_text

logger = logging.getLogger(__name__)

RANDOM_INLINE_RESULT_TITLE = "🎲 Выбрать случайную картинку"

type InlineMediaResult = (
    InlineQueryResultCachedGif | InlineQueryResultCachedPhoto | InlineQueryResultGif | InlineQueryResultPhoto
)


@dataclass(frozen=True, slots=True)
class InlineResultsPage:
    results: tuple[InlineMediaResult, ...]
    next_cursor: InlinePaginationCursor | None


router = Router(name="inline")


@router.inline_query()
async def answer_inline_query(inline_query: InlineQuery) -> None:
    normalized_query = normalize_category_search_term(inline_query.query)
    query_is_empty = not bool(normalized_query)
    with InlineQueryMetrics.start(query_is_empty=query_is_empty) as metrics:
        subscription_types = await _find_subscription_types(inline_query.query)
        metrics.counts.matched_categories = len(subscription_types)

        if not subscription_types:
            await _answer_inline_query(
                inline_query,
                [],
                next_cursor=None,
                normalized_query=normalized_query,
            )
            return

        next_cursor: InlinePaginationCursor | None = None
        try:
            if query_is_empty:
                results = await _get_inline_category_results(subscription_types)
            else:
                cursor = (
                    decode_inline_pagination_cursor(inline_query.offset, normalized_query)
                    if inline_query.offset
                    else None
                )
                page = await _get_inline_results(subscription_types, cursor=cursor)
                results = list(page.results)
                next_cursor = page.next_cursor
            metrics.counts.results_prepared = len(results)
        except InvalidInlinePaginationCursor as error:
            metrics.set_outcome("handled_error")
            logger.warning(
                "Rejected inline pagination cursor; correlation_id=%s; reason=%s",
                metrics.correlation_id,
                error,
            )
            results = []
        except Exception:
            metrics.set_outcome("handled_error")
            logger.exception("Failed to prepare inline results; correlation_id=%s", metrics.correlation_id)
            results = []

        if metrics.counts.url_failures and metrics.outcome == "success":
            metrics.set_outcome("partial_error")
        answer_results: list[InlineQueryResultUnion] = [*results[:MAX_INLINE_QUERY_RESULTS]]
        metrics.counts.results_sent = len(answer_results)
        await _answer_inline_query(
            inline_query,
            answer_results,
            next_cursor=next_cursor,
            normalized_query=normalized_query,
        )


@inline_query_stage(CATEGORIES_LOOKUP_STAGE)
async def _find_subscription_types(query: str) -> list[SubscriptionType]:
    metrics = get_inline_query_metrics()
    if metrics is not None:
        metrics.counts.postgres_calls += 1
    subscription_types = await get_scheduled_subscription_types()
    if not normalize_category_search_term(query):
        return subscription_types

    return [
        subscription_type
        for subscription_type in subscription_types
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


async def _get_inline_results(
    subscription_types: list[SubscriptionType],
    *,
    cursor: InlinePaginationCursor | None,
) -> InlineResultsPage:
    images_page = await get_inline_images(subscription_types, cursor=cursor)
    ordinary_results = _build_inline_results(list(images_page.ordinary_images))
    special_result = (
        _build_random_inline_result(*images_page.special_image) if images_page.special_image is not None else None
    )
    return InlineResultsPage(
        results=tuple([*([special_result] if special_result is not None else []), *ordinary_results]),
        next_cursor=images_page.next_cursor,
    )


async def _get_inline_category_results(subscription_types: list[SubscriptionType]) -> list[InlineMediaResult]:
    images = await get_inline_category_images(subscription_types)
    return _build_inline_category_results(images)


@inline_query_stage(RESULTS_PREPARE_STAGE)
def _build_inline_category_results(
    images: list[tuple[SubscriptionType, CachedMedia | LinkedMedia]],
) -> list[InlineMediaResult]:
    return [
        _inline_result(image, subscription_type=subscription_type).model_copy(
            update={
                "id": _category_result_id(subscription_type, image),
                "title": f"🎲 {format_category_button_text(subscription_type)}",
            }
        )
        for subscription_type, image in images
    ]


@inline_query_stage(RESULTS_PREPARE_STAGE)
def _build_inline_results(
    images: list[tuple[SubscriptionType, CachedMedia | LinkedMedia]],
) -> list[InlineMediaResult]:
    seen_paths: set[str] = set()
    results: list[InlineMediaResult] = []

    for subscription_type, image in images:
        if image.path in seen_paths:
            continue

        seen_paths.add(image.path)
        results.append(_inline_result(image, subscription_type=subscription_type))

    return results


@inline_query_stage(TELEGRAM_ANSWER_STAGE)
async def _answer_inline_query(
    inline_query: InlineQuery,
    results: list[InlineQueryResultUnion],
    *,
    next_cursor: InlinePaginationCursor | None,
    normalized_query: str,
) -> None:
    metrics = get_inline_query_metrics()
    if metrics is not None:
        metrics.counts.telegram_calls += 1
    next_offset = encode_inline_pagination_cursor(next_cursor, normalized_query) if next_cursor is not None else ""
    await inline_query.answer(results, cache_time=0, is_personal=True, next_offset=next_offset)


def _build_random_inline_result(
    subscription_type: SubscriptionType,
    image: CachedMedia | LinkedMedia,
) -> InlineMediaResult:
    result = _inline_result(image, subscription_type=subscription_type)
    return result.model_copy(
        update={
            "id": _random_result_id(result.id),
            "title": RANDOM_INLINE_RESULT_TITLE,
        }
    )


def _random_result_id(result_id: str) -> str:
    return hashlib.sha256(f"random:{result_id}".encode()).hexdigest()


def _category_result_id(subscription_type: SubscriptionType, image: CachedMedia | LinkedMedia) -> str:
    identity = f"category-random:{subscription_type.id}:{image.path}:{image.source_revision}"
    return hashlib.sha256(identity.encode()).hexdigest()


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

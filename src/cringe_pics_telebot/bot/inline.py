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
from cringe_pics_telebot.services.inline_images import CachedInlineImage, LinkedInlineImage, get_inline_images
from cringe_pics_telebot.services.subscriptions import get_subscription_types

logger = logging.getLogger(__name__)

router = Router(name="inline")


@router.inline_query()
async def answer_inline_query(inline_query: InlineQuery) -> None:
    subscription_type = await _find_subscription_type(inline_query.query)
    if subscription_type is None:
        await inline_query.answer([], cache_time=0, is_personal=True)
        return

    try:
        images = await get_inline_images(subscription_type)
        results = [_inline_result(image, subscription_type=subscription_type) for image in images]
    except Exception:
        logger.exception(
            "Failed to prepare inline results for user %d and category %s",
            inline_query.from_user.id,
            subscription_type.name,
        )
        results = []

    await inline_query.answer(results, cache_time=0, is_personal=True)


async def _find_subscription_type(query: str) -> SubscriptionType | None:
    normalized_query = _normalize_category(query)
    if not normalized_query:
        return None

    return next(
        (
            subscription_type
            for subscription_type in await get_subscription_types()
            if _normalize_category(subscription_type.name) == normalized_query
        ),
        None,
    )


def _normalize_category(category: str) -> str:
    return category.strip().removeprefix("/").casefold()


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

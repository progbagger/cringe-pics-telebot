from collections.abc import Sequence

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    SubscriptionType,
    get_category_media_by_subscription_types,
)
from cringe_pics_telebot.repositories.yandex import get_download_urls
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia

MAX_INLINE_QUERY_RESULTS = 50


async def get_inline_images(
    subscription_types: Sequence[SubscriptionType],
    *,
    limit_per_category: int | None = MAX_INLINE_QUERY_RESULTS,
) -> list[tuple[SubscriptionType, CachedMedia | LinkedMedia]]:
    media = await get_category_media_by_subscription_types([item.id for item in subscription_types])
    media_by_category: dict[int, list[CategoryMedia]] = {}
    for item in media:
        category_media = media_by_category.setdefault(item.subscription_type_id, [])
        if limit_per_category is None or len(category_media) < limit_per_category:
            category_media.append(item)

    selected_media: list[CategoryMedia] = []
    seen_paths: set[str] = set()
    for subscription_type in subscription_types:
        for item in media_by_category.get(subscription_type.id, []):
            if item.source_path not in seen_paths:
                seen_paths.add(item.source_path)
                selected_media.append(item)

    pending_paths = list(dict.fromkeys(item.source_path for item in selected_media if item.telegram_file_id is None))
    download_urls_by_path = dict(
        zip(
            pending_paths,
            await get_download_urls(pending_paths) if pending_paths else [],
            strict=True,
        )
    )

    subscription_types_by_id = {item.id: item for item in subscription_types}
    results: list[tuple[SubscriptionType, CachedMedia | LinkedMedia]] = []
    for item in selected_media:
        subscription_type = subscription_types_by_id[item.subscription_type_id]
        if item.telegram_file_id is not None:
            results.append((subscription_type, _cached_media(item)))
        elif (url := download_urls_by_path[item.source_path]) is not None:
            results.append((subscription_type, _linked_media(item, url)))
    return results


def _cached_media(media: CategoryMedia) -> CachedMedia:
    assert media.telegram_file_id is not None
    return CachedMedia(
        name=media.name,
        mime_type=media.mime_type,
        path=media.source_path,
        source_revision=media.source_revision,
        id=media.telegram_file_id,
    )


def _linked_media(media: CategoryMedia, url: str) -> LinkedMedia:
    return LinkedMedia(
        name=media.name,
        mime_type=media.mime_type,
        path=media.source_path,
        source_revision=media.source_revision,
        url=url,
    )

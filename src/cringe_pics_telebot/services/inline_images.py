from collections.abc import Sequence

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    SubscriptionType,
    get_category_media_by_subscription_types,
)
from cringe_pics_telebot.repositories.yandex import get_download_urls
from cringe_pics_telebot.services.random_image import CachedMedia, LinkedMedia

from .inline_metrics import (
    MEDIA_CATALOG_STAGE,
    MEDIA_URLS_STAGE,
    RESULTS_PREPARE_STAGE,
    get_inline_query_metrics,
    inline_query_stage,
)

MAX_INLINE_QUERY_RESULTS = 50


async def get_inline_images(
    subscription_types: Sequence[SubscriptionType],
    *,
    limit_per_category: int | None = MAX_INLINE_QUERY_RESULTS,
) -> list[tuple[SubscriptionType, CachedMedia | LinkedMedia]]:
    media = await _get_catalog_media([item.id for item in subscription_types])
    metrics = get_inline_query_metrics()
    if metrics is not None:
        metrics.counts.catalog_media = len(media)

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
    if metrics is not None:
        metrics.counts.selected_media = len(selected_media)
        metrics.counts.ready_media = len(selected_media) - len(pending_paths)
        metrics.counts.pending_media = len(pending_paths)

    if pending_paths:
        download_urls = await _get_media_urls(pending_paths)
        if metrics is not None:
            metrics.counts.url_successes += sum(url is not None for url in download_urls)
            metrics.counts.url_failures += sum(url is None for url in download_urls)
    else:
        download_urls = []
    download_urls_by_path = dict(zip(pending_paths, download_urls, strict=True))
    return _prepare_inline_image_results(
        selected_media,
        subscription_types=subscription_types,
        download_urls_by_path=download_urls_by_path,
    )


@inline_query_stage(MEDIA_CATALOG_STAGE)
async def _get_catalog_media(subscription_type_ids: list[int]) -> list[CategoryMedia]:
    metrics = get_inline_query_metrics()
    if metrics is not None:
        metrics.counts.postgres_calls += 1
    return await get_category_media_by_subscription_types(subscription_type_ids)


@inline_query_stage(MEDIA_URLS_STAGE)
async def _get_media_urls(paths: list[str]) -> list[str | None]:
    metrics = get_inline_query_metrics()
    if metrics is not None:
        metrics.counts.yandex_calls += len(paths)
    return await get_download_urls(paths)


@inline_query_stage(RESULTS_PREPARE_STAGE)
def _prepare_inline_image_results(
    selected_media: list[CategoryMedia],
    *,
    subscription_types: Sequence[SubscriptionType],
    download_urls_by_path: dict[str, str | None],
) -> list[tuple[SubscriptionType, CachedMedia | LinkedMedia]]:
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

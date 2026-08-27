import random
from collections.abc import Callable, MutableSequence, Sequence

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

type MediaChooser = Callable[[Sequence[CategoryMedia]], CategoryMedia]
type MediaShuffler = Callable[[MutableSequence[CategoryMedia]], None]


async def get_inline_images(
    subscription_types: Sequence[SubscriptionType],
    *,
    limit: int | None = MAX_INLINE_QUERY_RESULTS,
    shuffler: MediaShuffler | None = None,
) -> list[tuple[SubscriptionType, CachedMedia | LinkedMedia]]:
    media = await _get_catalog_media([item.id for item in subscription_types])
    _record_catalog_media_count(media)
    selected_media = _select_inline_media(
        media,
        subscription_types=subscription_types,
        limit=limit,
        shuffler=shuffler,
    )
    return await _resolve_inline_images(selected_media, subscription_types=subscription_types)


async def get_inline_category_images(
    subscription_types: Sequence[SubscriptionType],
    *,
    limit: int = MAX_INLINE_QUERY_RESULTS,
    chooser: MediaChooser | None = None,
) -> list[tuple[SubscriptionType, CachedMedia | LinkedMedia]]:
    media = await _get_catalog_media([item.id for item in subscription_types])
    _record_catalog_media_count(media)
    selected_media = _select_inline_category_media(
        media,
        subscription_types=subscription_types,
        limit=limit,
        chooser=chooser,
    )
    return await _resolve_inline_images(selected_media, subscription_types=subscription_types)


def _record_catalog_media_count(media: Sequence[CategoryMedia]) -> None:
    metrics = get_inline_query_metrics()
    if metrics is not None:
        metrics.counts.catalog_media = len(media)


def _select_inline_category_media(
    media: Sequence[CategoryMedia],
    *,
    subscription_types: Sequence[SubscriptionType],
    limit: int,
    chooser: MediaChooser | None = None,
) -> list[CategoryMedia]:
    media_by_category: dict[int, list[CategoryMedia]] = {}
    for item in media:
        media_by_category.setdefault(item.subscription_type_id, []).append(item)

    choose = chooser or random.choice
    selected: list[CategoryMedia] = []
    for subscription_type in subscription_types:
        candidates = media_by_category.get(subscription_type.id, [])
        ready = [item for item in candidates if item.telegram_file_id is not None]
        if preferred := ready or candidates:
            selected.append(choose(preferred))

    return selected[:limit]


def _select_inline_media(
    media: Sequence[CategoryMedia],
    *,
    subscription_types: Sequence[SubscriptionType],
    limit: int | None,
    shuffler: MediaShuffler | None = None,
) -> list[CategoryMedia]:
    media_by_category: dict[int, list[CategoryMedia]] = {}
    for item in media:
        media_by_category.setdefault(item.subscription_type_id, []).append(item)

    media_by_path: dict[str, CategoryMedia] = {}
    for subscription_type in subscription_types:
        for item in media_by_category.get(subscription_type.id, []):
            existing = media_by_path.get(item.source_path)
            if existing is None or (existing.telegram_file_id is None and item.telegram_file_id is not None):
                media_by_path[item.source_path] = item

    ready = [item for item in media_by_path.values() if item.telegram_file_id is not None]
    pending = [item for item in media_by_path.values() if item.telegram_file_id is None]
    shuffle = shuffler or random.shuffle
    shuffle(ready)
    shuffle(pending)

    selected = [*ready, *pending]
    return selected if limit is None else selected[:limit]


async def _resolve_inline_images(
    selected_media: list[CategoryMedia],
    *,
    subscription_types: Sequence[SubscriptionType],
) -> list[tuple[SubscriptionType, CachedMedia | LinkedMedia]]:
    pending_media = [item for item in selected_media if item.telegram_file_id is None]
    pending_paths = list(dict.fromkeys(item.source_path for item in pending_media))
    metrics = get_inline_query_metrics()
    if metrics is not None:
        metrics.counts.selected_media = len(selected_media)
        metrics.counts.ready_media = len(selected_media) - len(pending_media)
        metrics.counts.pending_media = len(pending_media)

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

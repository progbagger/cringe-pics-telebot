import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from cringe_pics_telebot.entities.search_aliases import SearchAlias
from cringe_pics_telebot.helpers.metrics import CounterMetric, Stopwatch, get_metrics_sink
from cringe_pics_telebot.pagination import InlineKeyboardPage, paginate_inline_keyboard
from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaSearchMetadata,
    SubscriptionType,
    get_category_media_search_metadata,
    get_category_media_search_metadata_by_subscription_type,
    get_subscription_type,
    materialize_category_media,
    transaction,
)
from cringe_pics_telebot.repositories.yandex import get_download_urls

from .media_search_aliases import set_media_search_aliases
from .random_image import CachedMedia, LinkedMedia

logger = logging.getLogger(__name__)

PREVIEW_URL_CONCURRENCY = 4
PREVIEW_URL_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class AdminMediaPreview:
    metadata: CategoryMediaSearchMetadata
    media: CachedMedia | LinkedMedia | None


@dataclass(frozen=True, slots=True)
class AdminMediaPage:
    category: SubscriptionType
    total: int
    page: InlineKeyboardPage[AdminMediaPreview]


@dataclass(frozen=True, slots=True)
class AdminMediaCatalog:
    category: SubscriptionType
    media: tuple[CategoryMediaSearchMetadata, ...]


async def get_admin_media_catalog(category_id: int) -> AdminMediaCatalog | None:
    category = await get_subscription_type(category_id)
    if category is None:
        return None

    media = await get_category_media_search_metadata_by_subscription_type(category_id, active_only=True)

    return AdminMediaCatalog(category=category, media=tuple(media))


async def get_admin_media_page(category_id: int, requested_page: int) -> AdminMediaPage | None:
    catalog = await get_admin_media_catalog(category_id)
    if catalog is None:
        return None

    page = paginate_inline_keyboard(catalog.media, requested_page)
    semaphore = asyncio.Semaphore(PREVIEW_URL_CONCURRENCY)

    async def prepare(metadata: CategoryMediaSearchMetadata) -> AdminMediaPreview:
        async with semaphore:
            return await prepare_admin_media_preview(metadata)

    previews = tuple(await asyncio.gather(*(prepare(metadata) for metadata in page.items)))

    return AdminMediaPage(
        category=catalog.category,
        total=len(catalog.media),
        page=InlineKeyboardPage(previews, page.number, page.previous_number, page.next_number),
    )


async def prepare_admin_media_preview(metadata: CategoryMediaSearchMetadata) -> AdminMediaPreview:
    try:
        with Stopwatch.start(metric_name="admin_media.preview_prepare"):
            preview = await get_admin_media_preview(metadata)
    except Exception as error:
        logger.warning("Admin media preview unavailable: media_id=%d error=%s", metadata.media.id, type(error).__name__)
        preview = None

    return AdminMediaPreview(metadata=metadata, media=preview)


async def get_admin_media(
    category_id: int,
    media_id: int,
) -> CategoryMediaSearchMetadata | None:
    metadata = await get_category_media_search_metadata(media_id)
    if metadata is None or metadata.media.subscription_type_id != category_id or not metadata.media.is_active:
        return None

    return metadata


async def update_admin_media_search_aliases(
    category_id: int,
    media_id: int,
    aliases: Sequence[SearchAlias],
) -> CategoryMediaSearchMetadata | None:
    return await set_media_search_aliases(
        media_id,
        aliases,
        subscription_type_id=category_id,
        active_only=True,
    )


async def get_admin_media_preview(
    metadata: CategoryMediaSearchMetadata,
) -> CachedMedia | LinkedMedia | None:
    media = metadata.media
    if media.telegram_file_id is not None:
        get_metrics_sink().emit((CounterMetric("admin_media.cache_hit"),))
        return CachedMedia(
            name=media.name,
            mime_type=media.mime_type,
            path=media.source_path,
            source_revision=media.source_revision,
            id=media.telegram_file_id,
        )

    get_metrics_sink().emit((CounterMetric("admin_media.cache_miss"),))
    with Stopwatch.start(metric_name="admin_media.url_lookup"):
        async with asyncio.timeout(PREVIEW_URL_TIMEOUT):
            (download_url,) = await get_download_urls((media.source_path,))
    if download_url is None:
        return None

    return LinkedMedia(
        name=media.name,
        mime_type=media.mime_type,
        path=media.source_path,
        source_revision=media.source_revision,
        url=download_url,
    )


@dataclass(frozen=True, slots=True)
class AdminMediaMaterialization:
    media_id: int
    source_revision: str
    file_id: str
    file_unique_id: str


async def materialize_admin_media(previews: Sequence[AdminMediaMaterialization]) -> None:
    for preview in previews:
        try:
            async with transaction():
                await materialize_category_media(
                    media_id=preview.media_id,
                    source_revision=preview.source_revision,
                    telegram_file_id=preview.file_id,
                    telegram_file_unique_id=preview.file_unique_id,
                )
        except Exception as error:
            logger.warning(
                "Admin media cache update failed: media_id=%d error=%s", preview.media_id, type(error).__name__
            )

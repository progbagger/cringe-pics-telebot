import logging
from collections.abc import Sequence
from dataclasses import dataclass

from cringe_pics_telebot.entities.search_aliases import SearchAlias
from cringe_pics_telebot.pagination import InlineKeyboardPage, paginate_inline_keyboard
from cringe_pics_telebot.repositories.postgres import (
    CategoryMediaSearchMetadata,
    SubscriptionType,
    get_category_media_search_metadata,
    get_category_media_search_metadata_by_subscription_type,
    get_subscription_type,
)
from cringe_pics_telebot.repositories.yandex import get_download_urls

from .media_search_aliases import set_media_search_aliases
from .random_image import CachedMedia, LinkedMedia

logger = logging.getLogger(__name__)


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
    previews = tuple([await prepare_admin_media_preview(metadata) for metadata in page.items])

    return AdminMediaPage(
        category=catalog.category,
        total=len(catalog.media),
        page=InlineKeyboardPage(previews, page.number, page.previous_number, page.next_number),
    )


async def prepare_admin_media_preview(metadata: CategoryMediaSearchMetadata) -> AdminMediaPreview:
    try:
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
        return CachedMedia(
            name=media.name,
            mime_type=media.mime_type,
            path=media.source_path,
            source_revision=media.source_revision,
            id=media.telegram_file_id,
        )

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

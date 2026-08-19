from collections.abc import Sequence
from datetime import UTC, datetime

from ..repositories.postgres.category_media import (
    deactivate_category_media_missing_from_snapshot,
    get_category_media_by_subscription_types,
    upsert_category_media_snapshot,
)
from ..repositories.postgres.connection import transaction
from ..repositories.postgres.entities.category_media import (
    CategoryMedia,
    CategoryMediaReconcileResult,
    CategoryMediaSource,
)


async def reconcile_category_media_snapshot(
    *,
    subscription_type_id: int,
    sources: Sequence[CategoryMediaSource],
    seen_at: datetime | None = None,
) -> CategoryMediaReconcileResult:
    sources_by_path = {source.source_path: source for source in sources}
    unique_sources = tuple(sources_by_path.values())
    source_paths = set(sources_by_path)
    now = seen_at or datetime.now(UTC)

    async with transaction():
        existing = await get_category_media_by_subscription_types(
            [subscription_type_id],
            active_only=False,
            with_for_update=True,
        )
        await upsert_category_media_snapshot(
            subscription_type_id=subscription_type_id,
            sources=unique_sources,
            seen_at=now,
        )
        deactivated = await deactivate_category_media_missing_from_snapshot(
            subscription_type_id=subscription_type_id,
            source_paths=source_paths,
            seen_at=now,
        )

    existing_by_path = {media.source_path: media for media in existing}
    created = sum(source.source_path not in existing_by_path for source in unique_sources)
    changed = sum(
        _source_changed(existing_by_path[source.source_path], source)
        for source in unique_sources
        if source.source_path in existing_by_path
    )
    reactivated = sum(
        not existing_by_path[source.source_path].is_active
        for source in unique_sources
        if source.source_path in existing_by_path
    )
    unchanged = sum(
        existing_by_path[source.source_path].is_active
        and not _source_changed(existing_by_path[source.source_path], source)
        for source in unique_sources
        if source.source_path in existing_by_path
    )
    return CategoryMediaReconcileResult(
        discovered=len(unique_sources),
        created=created,
        changed=changed,
        reactivated=reactivated,
        deactivated=deactivated,
        unchanged=unchanged,
    )


def _source_changed(media: CategoryMedia, source: CategoryMediaSource) -> bool:
    return (
        media.source_revision != source.source_revision
        or media.name != source.name
        or media.mime_type != source.mime_type
        or media.telegram_media_type != source.telegram_media_type
    )

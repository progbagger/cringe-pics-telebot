from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from pytest import MonkeyPatch

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaStatus,
    TelegramMediaType,
)
from cringe_pics_telebot.services import random_image


def test_choose_random_image_prefers_pending_and_injects_chooser() -> None:
    ready = _media(1, status=CategoryMediaStatus.ready)
    first_pending = _media(2, status=CategoryMediaStatus.pending)
    second_pending = _media(3, status=CategoryMediaStatus.pending)
    chooser_inputs: list[list[int]] = []

    def choose_last(items: Sequence[CategoryMedia]) -> CategoryMedia:
        chooser_inputs.append([item.id for item in items])
        return items[-1]

    selected = random_image.choose_random_image(
        [ready, first_pending, second_pending],
        chooser=choose_last,
    )

    assert selected is second_pending
    assert chooser_inputs == [[2, 3]]


@pytest.mark.parametrize(
    "status",
    [CategoryMediaStatus.pending, CategoryMediaStatus.ready],
)
def test_choose_random_image_chooses_from_single_status(status: CategoryMediaStatus) -> None:
    media = _media(1, status=status)

    assert random_image.choose_random_image([media], chooser=_choose_only) is media


@pytest.mark.parametrize("statuses", [(), (CategoryMediaStatus.inactive,)])
def test_choose_random_image_rejects_unavailable_candidate_set(
    statuses: tuple[CategoryMediaStatus, ...],
) -> None:
    with pytest.raises(random_image.NoCategoryMediaError):
        random_image.choose_random_image(
            [_media(index, status=status) for index, status in enumerate(statuses, start=1)]
        )


async def test_get_random_image_fetches_category_once_and_applies_policy(
    monkeypatch: MonkeyPatch,
) -> None:
    ready = _media(1, status=CategoryMediaStatus.ready)
    pending = _media(2, status=CategoryMediaStatus.pending)
    requested_category_ids: list[list[int] | None] = []

    async def get_media(category_ids: list[int] | None) -> list[CategoryMedia]:
        requested_category_ids.append(category_ids)
        return [ready, pending]

    monkeypatch.setattr(random_image, "get_category_media_by_subscription_types", get_media)

    assert await random_image.get_random_image(7, chooser=_choose_only) is pending
    assert requested_category_ids == [[7]]


def _choose_only(items: Sequence[CategoryMedia]) -> CategoryMedia:
    (selected,) = items
    return selected


def _media(media_id: int, *, status: CategoryMediaStatus) -> CategoryMedia:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    ready = status is CategoryMediaStatus.ready
    return CategoryMedia(
        id=media_id,
        subscription_type_id=7,
        source_path=f"day/{media_id}.png",
        source_revision=f"sha256:{media_id}",
        name=f"{media_id}.png",
        mime_type="image/png",
        telegram_media_type=TelegramMediaType.photo,
        telegram_file_id=f"telegram-{media_id}" if ready else None,
        telegram_file_unique_id=f"unique-{media_id}" if ready else None,
        is_active=status is not CategoryMediaStatus.inactive,
        status=status,
        last_seen_at=now,
        materialized_at=now if ready else None,
        created_at=now,
        updated_at=now,
    )

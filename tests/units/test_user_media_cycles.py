from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from hamcrest import assert_that, equal_to, same_instance

from cringe_pics_telebot.repositories.postgres import (
    CategoryMedia,
    CategoryMediaStatus,
    TelegramMediaType,
)
from cringe_pics_telebot.services.random_image import NoCategoryMediaError
from cringe_pics_telebot.services.user_media_cycles import (
    UserMediaCycleBusyError,
    choose_user_media_cycle_image,
)


def test_cycle_chooses_only_unshown_unreserved_media_and_then_applies_pending_priority() -> None:
    shown_ready = _media(1, status=CategoryMediaStatus.ready)
    remaining_ready = _media(2, status=CategoryMediaStatus.ready)
    remaining_pending = _media(3, status=CategoryMediaStatus.pending)
    reserved_pending = _media(4, status=CategoryMediaStatus.pending)
    chooser_inputs: list[list[int]] = []

    def choose_first(items: Sequence[CategoryMedia]) -> CategoryMedia:
        chooser_inputs.append([item.id for item in items])
        return items[0]

    selection = choose_user_media_cycle_image(
        [shown_ready, remaining_ready, remaining_pending, reserved_pending],
        shown_media_ids={shown_ready.id},
        reserved_media_ids={reserved_pending.id},
        last_media_id=shown_ready.id,
        chooser=choose_first,
    )

    assert_that(selection.media, same_instance(remaining_pending))
    assert_that(selection.starts_new_cycle, equal_to(False))
    assert_that(chooser_inputs, equal_to([[remaining_pending.id]]))


def test_new_media_is_added_to_current_cycle_while_removed_history_is_ignored() -> None:
    existing = _media(1, status=CategoryMediaStatus.ready)
    added = _media(3, status=CategoryMediaStatus.ready)

    selection = choose_user_media_cycle_image(
        [existing, added],
        shown_media_ids={existing.id, 2},
        reserved_media_ids=set(),
        last_media_id=existing.id,
        chooser=_choose_only,
    )

    assert_that(selection.media, same_instance(added))
    assert_that(selection.starts_new_cycle, equal_to(False))


def test_completed_cycle_excludes_last_media_from_new_cycle_boundary() -> None:
    first = _media(1, status=CategoryMediaStatus.ready)
    last = _media(2, status=CategoryMediaStatus.ready)

    selection = choose_user_media_cycle_image(
        [first, last],
        shown_media_ids={first.id, last.id},
        reserved_media_ids=set(),
        last_media_id=last.id,
        chooser=_choose_only,
    )

    assert_that(selection.media, same_instance(first))
    assert_that(selection.starts_new_cycle, equal_to(True))


def test_removed_media_does_not_block_new_single_item_cycle() -> None:
    remaining = _media(1, status=CategoryMediaStatus.ready)

    selection = choose_user_media_cycle_image(
        [remaining],
        shown_media_ids={remaining.id, 2},
        reserved_media_ids=set(),
        last_media_id=remaining.id,
        chooser=_choose_only,
    )

    assert_that(selection.media, same_instance(remaining))
    assert_that(selection.starts_new_cycle, equal_to(True))


def test_cycle_does_not_reset_while_all_remaining_media_are_reserved() -> None:
    media = _media(1, status=CategoryMediaStatus.ready)

    with pytest.raises(UserMediaCycleBusyError):
        choose_user_media_cycle_image(
            [media],
            shown_media_ids=set(),
            reserved_media_ids={media.id},
            last_media_id=None,
        )


@pytest.mark.parametrize("statuses", [(), (CategoryMediaStatus.inactive,)])
def test_cycle_rejects_empty_active_media(statuses: tuple[CategoryMediaStatus, ...]) -> None:
    with pytest.raises(NoCategoryMediaError):
        choose_user_media_cycle_image(
            tuple(_media(index, status=status) for index, status in enumerate(statuses, start=1)),
            shown_media_ids=set(),
            reserved_media_ids=set(),
            last_media_id=None,
        )


def _choose_only(items: Sequence[CategoryMedia]) -> CategoryMedia:
    (selected,) = items
    return selected


def _media(media_id: int, *, status: CategoryMediaStatus) -> CategoryMedia:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    ready = status is CategoryMediaStatus.ready
    return CategoryMedia(
        id=media_id,
        subscription_type_id=1,
        source_path=f"cycle/{media_id}.png",
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

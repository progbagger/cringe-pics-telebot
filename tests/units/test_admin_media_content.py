from typing import Any

import pytest
from aiogram.types import Message
from hamcrest import assert_that, empty, equal_to

from cringe_pics_telebot.bot.admin_media_content import create_admin_media_content, get_admin_media_materializations
from cringe_pics_telebot.services.admin_media import AdminMediaMaterialization, AdminMediaPreview
from cringe_pics_telebot.services.random_image import LinkedMedia
from tests.units.test_admin_media import _metadata


def _previews() -> tuple[AdminMediaPreview, ...]:
    return tuple(
        AdminMediaPreview(
            _metadata(index),
            LinkedMedia(
                name="same.png",
                mime_type="image/png",
                path=f"day/{index}.png",
                source_revision=f"sha256:{index}",
                url=f"https://example.test/{index}",
            )
            if index != 2
            else None,
        )
        for index in (1, 2, 3)
    )


def _response() -> Message:
    blocks: list[dict[str, Any]] = [{"type": "paragraph", "text": "text"}]
    for index in (1, 2, 3):
        blocks.append({"type": "paragraph", "text": f"{index}. same.png"})
        blocks.append(
            {"type": "paragraph", "text": "unavailable"}
            if index == 2
            else {
                "type": "photo",
                "photo": [
                    {"file_id": f"small-{index}", "file_unique_id": f"small-unique-{index}", "width": 1, "height": 1},
                    {
                        "file_id": f"large-{index}",
                        "file_unique_id": f"large-unique-{index}",
                        "width": 100,
                        "height": 100,
                    },
                ],
            }
        )
    return Message.model_validate(
        {"message_id": 1, "date": 1, "chat": {"id": 42, "type": "private"}, "rich_message": {"blocks": blocks}}
    )


def test_materialization_maps_block_positions_despite_duplicate_names_and_missing_preview() -> None:
    # arrange
    previews = _previews()
    content = create_admin_media_content("text", previews, numbered=True)
    response = _response()

    # act
    result = get_admin_media_materializations(content, response, previews)

    # assert
    assert_that(
        result,
        equal_to(
            tuple(
                AdminMediaMaterialization(index, f"sha256:{index}", f"large-{index}", f"large-unique-{index}")
                for index in (1, 3)
            )
        ),
    )


@pytest.mark.parametrize("change", ["true", "no-rich", "missing-block", "wrong-type", "empty-photo"])
def test_materialization_rejects_response_without_unambiguous_media_positions(change: str) -> None:
    # arrange
    previews = _previews()
    content = create_admin_media_content("text", previews, numbered=True)
    data = _response().model_dump()
    match change:
        case "no-rich":
            data["rich_message"] = None
        case "missing-block":
            data["rich_message"]["blocks"].pop(2)
        case "wrong-type":
            data["rich_message"]["blocks"][2] = {"type": "paragraph", "text": "changed"}
        case "empty-photo":
            data["rich_message"]["blocks"][2]["photo"] = []
    response = True if change == "true" else Message.model_validate(data)

    # act
    result = get_admin_media_materializations(content, response, previews)

    # assert
    assert_that(result, empty())

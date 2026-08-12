from datetime import UTC, datetime, time

import pytest
from pytest import MonkeyPatch

from cringe_pics_telebot.bot import inline
from cringe_pics_telebot.repositories.postgres import SubscriptionType
from cringe_pics_telebot.services.inline_images import CachedInlineImage, LinkedInlineImage


@pytest.mark.parametrize(
    ("query", "category", "matches"),
    [
        ("day", "/day", True),
        ("  DA ", "/day", True),
        ("/d", "/day", True),
        ("ING", "/evening", True),
        ("", "/day", False),
        (" / ", "/day", False),
        ("night", "/day", False),
    ],
)
def test_category_matches_query(query: str, category: str, matches: bool) -> None:
    assert inline.category_matches_query(query, category) is matches


async def test_find_subscription_types_returns_every_matching_category(monkeypatch: MonkeyPatch) -> None:
    subscription_types = [
        _subscription_type(1, "/morning", "morning"),
        _subscription_type(2, "/day", "day"),
        _subscription_type(3, "/evening", "evening"),
        _subscription_type(4, "/night", "night"),
    ]

    async def get_subscription_types() -> list[SubscriptionType]:
        return subscription_types

    monkeypatch.setattr(inline, "get_subscription_types", get_subscription_types)

    assert await inline._find_subscription_types("  ING ") == [subscription_types[0], subscription_types[2]]


async def test_get_inline_results_combines_categories_without_duplicate_paths(monkeypatch: MonkeyPatch) -> None:
    morning = _subscription_type(1, "/morning", "morning")
    evening = _subscription_type(3, "/evening", "evening")

    async def get_inline_images(subscription_type: SubscriptionType) -> list[CachedInlineImage | LinkedInlineImage]:
        if subscription_type == morning:
            return [
                CachedInlineImage(
                    name="shared.png",
                    mime_type="image/png",
                    path="shared/image.png",
                    file_id="telegram-shared",
                ),
                LinkedInlineImage(
                    name="morning.png",
                    mime_type="image/png",
                    path="morning/image.png",
                    url="https://storage.example/morning.png",
                ),
            ]

        return [
            LinkedInlineImage(
                name="duplicate.png",
                mime_type="image/png",
                path="shared/image.png",
                url="https://storage.example/duplicate.png",
            ),
            LinkedInlineImage(
                name="evening.png",
                mime_type="image/png",
                path="evening/image.png",
                url="https://storage.example/evening.png",
            ),
        ]

    monkeypatch.setattr(inline, "get_inline_images", get_inline_images)

    results = await inline._get_inline_results([morning, evening])
    payloads = [result.model_dump(exclude_none=True) for result in results]

    assert [payload["title"] for payload in payloads] == ["shared.png", "morning.png", "evening.png"]
    assert payloads[0]["description"] == "Категория /morning"
    assert payloads[2]["description"] == "Категория /evening"
    assert len({payload["id"] for payload in payloads}) == 3


def _subscription_type(subscription_type_id: int, name: str, directory: str) -> SubscriptionType:
    now = datetime.now(UTC)
    return SubscriptionType(
        id=subscription_type_id,
        name=name,
        time=time(13, 0, tzinfo=UTC),
        s3_directory_path=directory,
        created_at=now,
        updated_at=now,
    )

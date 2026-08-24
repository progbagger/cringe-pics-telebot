import json
import math
import os
import statistics
from asyncio import subprocess
from collections.abc import Awaitable, Callable, Sequence
from datetime import time
from functools import partial
from typing import Any

import pytest

from cringe_pics_telebot.services.media_sync import MediaSyncSummary
from tests.functional.conftest import (
    FakeStatsDServer,
    FakeTelegramServer,
    FakeYandexServer,
    FunctionalSubscriptionType,
)

RUNS_PER_SCENARIO = 20
COMMON_STAGES = (
    "categories.lookup",
    "media.catalog",
    "results.prepare",
    "telegram.answer",
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INLINE_LATENCY_BASELINE") != "1",
    reason="set RUN_INLINE_LATENCY_BASELINE=1 to collect the non-gating latency baseline",
)


async def test_collect_inline_latency_baseline(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
    fake_yandex_server: FakeYandexServer,
    fake_statsd_server: FakeStatsDServer,
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    synchronize_functional_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
) -> None:
    scenarios: dict[str, dict[str, Any]] = {}

    await _prepare_catalog(
        reset_dependency_state=reset_dependency_state,
        fake_yandex_server=fake_yandex_server,
        synchronize_media_catalog=synchronize_functional_media_catalog,
        subscription_types=(FunctionalSubscriptionType(1, "/small", time(0), "small"),),
        directories={"small": [{"name": "first.png"}, {"name": "second.png"}]},
    )
    scenarios["small_pending"] = await _measure_scenario(
        query="small",
        expected_results=2,
        expected_counts={
            "dependencies.postgres.calls": 2,
            "dependencies.yandex.calls": 2,
            "dependencies.redis.calls": 0,
            "dependencies.telegram.calls": 1,
            "media.catalog_items": 2,
            "results.sent": 2,
        },
        stages=(*COMMON_STAGES, "media.urls"),
        fake_telegram_server=fake_telegram_server,
        fake_statsd_server=fake_statsd_server,
    )

    await _prepare_catalog(
        reset_dependency_state=reset_dependency_state,
        fake_yandex_server=fake_yandex_server,
        synchronize_media_catalog=synchronize_functional_media_catalog,
        subscription_types=(FunctionalSubscriptionType(1, "/warm", time(0), "warm"),),
        directories={"warm": [{"name": "only.png"}]},
    )
    await fake_telegram_server.push_message(text="/warm")
    await fake_telegram_server.wait_for_request("editMessageMedia")
    scenarios["small_catalog_only"] = await _measure_scenario(
        query="warm",
        expected_results=1,
        expected_counts={
            "dependencies.postgres.calls": 2,
            "dependencies.yandex.calls": 0,
            "dependencies.redis.calls": 0,
            "dependencies.telegram.calls": 1,
            "media.catalog_items": 1,
            "results.sent": 1,
        },
        stages=COMMON_STAGES,
        fake_telegram_server=fake_telegram_server,
        fake_statsd_server=fake_statsd_server,
    )

    await _prepare_catalog(
        reset_dependency_state=reset_dependency_state,
        fake_yandex_server=fake_yandex_server,
        synchronize_media_catalog=synchronize_functional_media_catalog,
        subscription_types=(FunctionalSubscriptionType(1, "/mixed", time(0), "mixed"),),
        directories={"mixed": [{"name": "ready.png"}, {"name": "pending.png"}]},
    )
    await fake_telegram_server.push_message(text="/mixed")
    await fake_telegram_server.wait_for_request("editMessageMedia")
    scenarios["small_mixed"] = await _measure_scenario(
        query="mixed",
        expected_results=2,
        expected_counts={
            "dependencies.postgres.calls": 2,
            "dependencies.yandex.calls": 1,
            "dependencies.redis.calls": 0,
            "dependencies.telegram.calls": 1,
            "media.catalog_items": 2,
            "results.sent": 2,
        },
        stages=(*COMMON_STAGES, "media.urls"),
        fake_telegram_server=fake_telegram_server,
        fake_statsd_server=fake_statsd_server,
    )

    await _prepare_catalog(
        reset_dependency_state=reset_dependency_state,
        fake_yandex_server=fake_yandex_server,
        synchronize_media_catalog=synchronize_functional_media_catalog,
        subscription_types=(FunctionalSubscriptionType(1, "/large", time(0), "large"),),
        directories={"large": [{"name": f"image-{index}.png"} for index in range(60)]},
    )
    scenarios["large_pending"] = await _measure_scenario(
        query="large",
        expected_results=50,
        expected_counts={
            "dependencies.postgres.calls": 2,
            "dependencies.yandex.calls": 60,
            "dependencies.redis.calls": 0,
            "dependencies.telegram.calls": 1,
            "media.catalog_items": 60,
            "results.sent": 50,
        },
        stages=(*COMMON_STAGES, "media.urls"),
        fake_telegram_server=fake_telegram_server,
        fake_statsd_server=fake_statsd_server,
    )

    await _prepare_catalog(
        reset_dependency_state=reset_dependency_state,
        fake_yandex_server=fake_yandex_server,
        synchronize_media_catalog=synchronize_functional_media_catalog,
        subscription_types=(
            FunctionalSubscriptionType(1, "/first", time(0), "first", ("shared",)),
            FunctionalSubscriptionType(2, "/second", time(0), "second", ("shared",)),
        ),
        directories={
            "first": [{"name": "first.png"}],
            "second": [{"name": "second.png"}],
        },
    )
    scenarios["multiple_categories_pending"] = await _measure_scenario(
        query="shared",
        expected_results=2,
        expected_counts={
            "dependencies.postgres.calls": 2,
            "dependencies.yandex.calls": 2,
            "dependencies.redis.calls": 0,
            "dependencies.telegram.calls": 1,
            "media.catalog_items": 2,
            "results.sent": 2,
        },
        stages=(*COMMON_STAGES, "media.urls"),
        fake_telegram_server=fake_telegram_server,
        fake_statsd_server=fake_statsd_server,
    )

    print(json.dumps({"runs_per_scenario": RUNS_PER_SCENARIO, "scenarios": scenarios}, sort_keys=True))


async def _prepare_catalog(
    *,
    reset_dependency_state: Callable[[tuple[FunctionalSubscriptionType, ...]], Awaitable[None]],
    fake_yandex_server: FakeYandexServer,
    synchronize_media_catalog: Callable[[], Awaitable[MediaSyncSummary]],
    subscription_types: tuple[FunctionalSubscriptionType, ...],
    directories: dict[str, list[dict[str, Any]]],
) -> None:
    await reset_dependency_state(subscription_types)
    for directory, images in directories.items():
        await fake_yandex_server.configure_directory(directory, images=images)
    await synchronize_media_catalog()
    await fake_yandex_server.reset()


async def _measure_scenario(
    *,
    query: str,
    expected_results: int,
    expected_counts: dict[str, int],
    stages: Sequence[str],
    fake_telegram_server: FakeTelegramServer,
    fake_statsd_server: FakeStatsDServer,
) -> dict[str, Any]:
    measurements: list[dict[str, float]] = []
    observed_counts: dict[str, int] = {}
    for run in range(RUNS_PER_SCENARIO):
        await fake_telegram_server.reset()
        await fake_statsd_server.reset()
        query_id = f"baseline-{query}-{run}"
        await fake_telegram_server.push_inline_query(query=query, query_id=query_id)
        answer = await fake_telegram_server.wait_for_request(
            "answerInlineQuery",
            predicate=partial(_has_inline_query_id, query_id=query_id),
        )
        assert len(answer["payload"]["results"]) == expected_results

        measurement = {"total": float((await fake_statsd_server.wait_for_metric("functional.inline.total"))["value"])}
        for stage in stages:
            metric = await fake_statsd_server.wait_for_metric(f"functional.inline.stages.{stage}")
            measurement[stage] = float(metric["value"])
        for name, expected_value in expected_counts.items():
            metric = await fake_statsd_server.wait_for_metric(f"functional.inline.{name}")
            value = int(metric["value"])
            assert value == expected_value
            if run == 0:
                observed_counts[name] = value
        measurements.append(measurement)

    metric_names = measurements[0]
    return {
        "first_total_ms": round(measurements[0]["total"], 3),
        "repeated_total_ms": _summary([measurement["total"] for measurement in measurements[1:]]),
        "all_runs_ms": {name: _summary([measurement[name] for measurement in measurements]) for name in metric_names},
        "counts_per_request": observed_counts,
    }


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = math.ceil(0.95 * len(ordered)) - 1
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
    }


def _has_inline_query_id(request: dict[str, Any], *, query_id: str) -> bool:
    return request["payload"].get("inline_query_id") == query_id

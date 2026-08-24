import json
import logging
from collections.abc import Iterable

import pytest

from cringe_pics_telebot.helpers.metrics import CounterMetric, Metric, TimingMetric
from cringe_pics_telebot.services.inline_metrics import (
    CATEGORIES_LOOKUP_STAGE,
    InlineQueryMetrics,
    inline_query_stage,
)


def test_inline_metrics_emit_correlated_stage_scenario_and_dependency_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink = _RecordingMetricsSink()
    clock = iter((1.0, 1.1, 1.2, 1.5)).__next__
    with caplog.at_level(logging.INFO):
        with InlineQueryMetrics.start(
            query_is_empty=False,
            sink=sink,
            clock=clock,
            correlation_id="correlation-1",
        ) as metrics:
            metrics.counts.matched_categories = 2
            metrics.counts.catalog_media = 3
            metrics.counts.selected_media = 3
            metrics.counts.ready_media = 1
            metrics.counts.pending_media = 2
            metrics.counts.url_successes = 1
            metrics.counts.url_failures = 1
            metrics.counts.results_prepared = 2
            metrics.counts.results_sent = 2
            metrics.counts.postgres_calls = 2
            metrics.counts.yandex_calls = 2
            metrics.counts.telegram_calls = 1
            metrics.set_outcome("partial_error")

            with metrics.stage(CATEGORIES_LOOKUP_STAGE):
                pass
        metrics.finish()

    assert len(sink.batches) == 1
    emitted = sink.batches[0]
    assert CounterMetric("inline.requests") in emitted
    assert CounterMetric("inline.outcomes.partial_error.requests") in emitted
    assert CounterMetric("inline.scenarios.mixed.requests") in emitted
    assert CounterMetric("inline.category_sets.multiple.requests") in emitted
    assert CounterMetric("inline.catalog_sizes.small.requests") in emitted
    assert CounterMetric("inline.dependencies.postgres.calls", 2) in emitted
    assert CounterMetric("inline.dependencies.yandex.calls", 2) in emitted
    assert CounterMetric("inline.dependencies.redis.calls", 0) in emitted
    assert CounterMetric("inline.dependencies.telegram.calls", 1) in emitted
    timings = {metric.name: metric.milliseconds for metric in emitted if isinstance(metric, TimingMetric)}
    assert timings["inline.stages.categories.lookup"] == pytest.approx(100)
    assert timings["inline.total"] == pytest.approx(500)

    event = json.loads(caplog.messages[-1])
    assert event == {
        "catalog_size": "small",
        "category_set": "multiple",
        "correlation_id": "correlation-1",
        "counts": {
            "catalog_media": 3,
            "matched_categories": 2,
            "pending_media": 2,
            "postgres_calls": 2,
            "ready_media": 1,
            "redis_calls": 0,
            "results_prepared": 2,
            "results_sent": 2,
            "retries": 0,
            "selected_media": 3,
            "telegram_calls": 1,
            "url_failures": 1,
            "url_successes": 1,
            "yandex_calls": 2,
        },
        "durations_ms": {"categories.lookup": 100.0, "total": 500.0},
        "event": "inline_query_metrics",
        "outcome": "partial_error",
        "scenario": "mixed",
    }


@pytest.mark.parametrize(
    ("query_is_empty", "matched", "catalog", "ready", "pending", "scenario", "category_set", "size"),
    [
        (True, 0, 0, 0, 0, "empty_query", "none", "empty"),
        (False, 0, 0, 0, 0, "unknown_category", "none", "empty"),
        (False, 1, 0, 0, 0, "known_empty", "single", "empty"),
        (False, 1, 2, 2, 0, "catalog_only", "single", "small"),
        (False, 1, 2, 0, 2, "pending_only", "single", "small"),
        (False, 2, 51, 1, 50, "mixed", "multiple", "large"),
    ],
)
def test_inline_metrics_classify_low_cardinality_scenarios(
    query_is_empty: bool,
    matched: int,
    catalog: int,
    ready: int,
    pending: int,
    scenario: str,
    category_set: str,
    size: str,
) -> None:
    with InlineQueryMetrics.start(query_is_empty=query_is_empty, clock=lambda: 1) as metrics:
        metrics.counts.matched_categories = matched
        metrics.counts.catalog_media = catalog
        metrics.counts.ready_media = ready
        metrics.counts.pending_media = pending

        assert metrics.scenario == scenario
        assert metrics.category_set == category_set
        assert metrics.catalog_size == size


async def test_inline_stage_decorator_times_sync_and_async_functions() -> None:
    sink = _RecordingMetricsSink()
    clock = iter((0.0, 1.0, 1.1, 2.0, 2.2, 3.0)).__next__

    @inline_query_stage("sync")
    def sync_function(value: int) -> int:
        return value + 1

    @inline_query_stage("async")
    async def async_function(value: int) -> int:
        return value + 2

    with InlineQueryMetrics.start(query_is_empty=True, sink=sink, clock=clock):
        assert sync_function(1) == 2
        assert await async_function(1) == 3

    emitted = sink.batches[0]
    timings = {metric.name: metric.milliseconds for metric in emitted if isinstance(metric, TimingMetric)}
    assert timings["inline.stages.sync"] == pytest.approx(100)
    assert timings["inline.stages.async"] == pytest.approx(200)


class _RecordingMetricsSink:
    def __init__(self) -> None:
        self.batches: list[list[Metric]] = []

    def emit(self, metrics: Iterable[Metric]) -> None:
        self.batches.append(list(metrics))

    def close(self) -> None:
        pass

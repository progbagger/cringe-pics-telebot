import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import wraps
from typing import Any, cast

from cringe_pics_telebot.helpers.metrics import (
    Clock,
    CounterMetric,
    Metric,
    MetricsSink,
    TimingMetric,
    get_metrics_sink,
)

logger = logging.getLogger(__name__)

CATEGORIES_LOOKUP_STAGE = "categories.lookup"
MEDIA_CATALOG_STAGE = "media.catalog"
MEDIA_URLS_STAGE = "media.urls"
RESULTS_PREPARE_STAGE = "results.prepare"
TELEGRAM_ANSWER_STAGE = "telegram.answer"


@dataclass(slots=True)
class InlineQueryCounts:
    matched_categories: int = 0
    catalog_media: int = 0
    selected_media: int = 0
    ready_media: int = 0
    pending_media: int = 0
    url_successes: int = 0
    url_failures: int = 0
    results_prepared: int = 0
    results_sent: int = 0
    postgres_calls: int = 0
    yandex_calls: int = 0
    redis_calls: int = 0
    telegram_calls: int = 0
    retries: int = 0


@dataclass(slots=True)
class InlineQueryMetrics:
    correlation_id: str
    query_is_empty: bool
    counts: InlineQueryCounts
    _sink: MetricsSink
    _clock: Clock
    _started_at: float
    _outcome: str = "success"
    _stage_durations_ms: dict[str, float] = field(default_factory=dict)
    _finished: bool = False

    @classmethod
    @contextmanager
    def start(
        cls,
        *,
        query_is_empty: bool,
        sink: MetricsSink | None = None,
        clock: Clock = time.monotonic,
        correlation_id: str | None = None,
    ) -> Iterator[InlineQueryMetrics]:
        metrics = cls(
            correlation_id=correlation_id or uuid.uuid4().hex,
            query_is_empty=query_is_empty,
            counts=InlineQueryCounts(),
            _sink=sink or get_metrics_sink(),
            _clock=clock,
            _started_at=clock(),
        )
        try:
            with _current_inline_metrics.set(metrics):
                try:
                    yield metrics
                except asyncio.CancelledError:
                    metrics.set_outcome("cancelled")
                    raise
                except Exception:
                    metrics.set_outcome("unhandled_error")
                    raise
        finally:
            metrics.finish()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started_at = self._clock()
        try:
            yield
        finally:
            elapsed_ms = (self._clock() - started_at) * 1_000
            self._stage_durations_ms[name] = self._stage_durations_ms.get(name, 0) + elapsed_ms

    def set_outcome(self, outcome: str) -> None:
        self._outcome = outcome

    @property
    def outcome(self) -> str:
        return self._outcome

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True

        total_ms = (self._clock() - self._started_at) * 1_000
        scenario = self.scenario
        category_set = self.category_set
        catalog_size = self.catalog_size
        durations_ms = {"total": total_ms, **self._stage_durations_ms}
        self._sink.emit(
            self._metrics(
                total_ms=total_ms,
                scenario=scenario,
                category_set=category_set,
                catalog_size=catalog_size,
            )
        )
        logger.info(
            json.dumps(
                {
                    "event": "inline_query_metrics",
                    "correlation_id": self.correlation_id,
                    "outcome": self._outcome,
                    "scenario": scenario,
                    "category_set": category_set,
                    "catalog_size": catalog_size,
                    "durations_ms": {name: round(value, 3) for name, value in durations_ms.items()},
                    "counts": asdict(self.counts),
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    @property
    def scenario(self) -> str:
        if self.query_is_empty:
            return "empty_query"
        if self.counts.matched_categories == 0:
            return "unknown_category"
        if self.counts.catalog_media == 0:
            return "known_empty"
        if self.counts.ready_media and self.counts.pending_media:
            return "mixed"
        if self.counts.pending_media:
            return "pending_only"
        return "catalog_only"

    @property
    def category_set(self) -> str:
        if self.counts.matched_categories == 0:
            return "none"
        if self.counts.matched_categories == 1:
            return "single"
        return "multiple"

    @property
    def catalog_size(self) -> str:
        if self.counts.catalog_media == 0:
            return "empty"
        if self.counts.catalog_media <= 50:
            return "small"
        return "large"

    def _metrics(
        self,
        *,
        total_ms: float,
        scenario: str,
        category_set: str,
        catalog_size: str,
    ) -> list[Metric]:
        metrics: list[Metric] = [
            CounterMetric("inline.requests"),
            CounterMetric(f"inline.outcomes.{self._outcome}.requests"),
            CounterMetric(f"inline.scenarios.{scenario}.requests"),
            CounterMetric(f"inline.category_sets.{category_set}.requests"),
            CounterMetric(f"inline.catalog_sizes.{catalog_size}.requests"),
            TimingMetric("inline.total", total_ms),
            TimingMetric(f"inline.outcomes.{self._outcome}.total", total_ms),
            TimingMetric(f"inline.scenarios.{scenario}.total", total_ms),
            TimingMetric(f"inline.category_sets.{category_set}.total", total_ms),
            TimingMetric(f"inline.catalog_sizes.{catalog_size}.total", total_ms),
        ]
        metrics.extend(
            TimingMetric(f"inline.stages.{name}", milliseconds)
            for name, milliseconds in self._stage_durations_ms.items()
        )
        metrics.extend(
            [
                CounterMetric("inline.media.catalog_items", self.counts.catalog_media),
                CounterMetric("inline.media.selected_items", self.counts.selected_media),
                CounterMetric("inline.media.ready_items", self.counts.ready_media),
                CounterMetric("inline.media.pending_items", self.counts.pending_media),
                CounterMetric("inline.media.url_successes", self.counts.url_successes),
                CounterMetric("inline.media.url_failures", self.counts.url_failures),
                CounterMetric("inline.results.prepared", self.counts.results_prepared),
                CounterMetric("inline.results.sent", self.counts.results_sent),
                CounterMetric("inline.dependencies.postgres.calls", self.counts.postgres_calls),
                CounterMetric("inline.dependencies.yandex.calls", self.counts.yandex_calls),
                CounterMetric("inline.dependencies.redis.calls", self.counts.redis_calls),
                CounterMetric("inline.dependencies.telegram.calls", self.counts.telegram_calls),
                CounterMetric("inline.retries", self.counts.retries),
            ]
        )
        return metrics


_current_inline_metrics: ContextVar[InlineQueryMetrics | None] = ContextVar(
    "current_inline_metrics",
    default=None,
)


def get_inline_query_metrics() -> InlineQueryMetrics | None:
    return _current_inline_metrics.get()


def inline_query_stage[**P, R](name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(function):
            async_function = cast(Callable[P, Awaitable[Any]], function)

            @wraps(function)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                metrics = get_inline_query_metrics()
                if metrics is None:
                    return await async_function(*args, **kwargs)
                with metrics.stage(name):
                    return await async_function(*args, **kwargs)

            return cast(Callable[P, R], async_wrapper)

        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            metrics = get_inline_query_metrics()
            if metrics is None:
                return function(*args, **kwargs)
            with metrics.stage(name):
                return function(*args, **kwargs)

        return wrapper

    return decorate

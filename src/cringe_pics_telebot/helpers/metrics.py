import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self

from statsd import StatsClient

logger = logging.getLogger(__name__)

DEFAULT_STATSD_PORT = 8125
DEFAULT_STATSD_PREFIX = "cringe_pics_telebot"

type Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class TimingMetric:
    name: str
    milliseconds: float


@dataclass(frozen=True, slots=True)
class CounterMetric:
    name: str
    value: int = 1


@dataclass(frozen=True, slots=True)
class GaugeMetric:
    name: str
    value: int | float


type Metric = TimingMetric | CounterMetric | GaugeMetric


class MetricsSink(Protocol):
    def emit(self, metrics: Iterable[Metric]) -> None: ...

    def close(self) -> None: ...


class _StatsDPipeline(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def timing(self, stat: str, delta: float) -> None: ...

    def incr(self, stat: str, count: int = 1) -> None: ...

    def gauge(self, stat: str, value: int | float) -> None: ...


class _StatsDClient(Protocol):
    def pipeline(self) -> _StatsDPipeline: ...

    def close(self) -> None: ...


class NullMetricsSink:
    def emit(self, metrics: Iterable[Metric]) -> None:
        pass

    def close(self) -> None:
        pass


class StatsDMetricsSink:
    def __init__(self, client: _StatsDClient) -> None:
        self._client = client

    def emit(self, metrics: Iterable[Metric]) -> None:
        try:
            with self._client.pipeline() as pipeline:
                for metric in metrics:
                    match metric:
                        case TimingMetric():
                            pipeline.timing(metric.name, metric.milliseconds)
                        case CounterMetric():
                            pipeline.incr(metric.name, metric.value)
                        case GaugeMetric():
                            pipeline.gauge(metric.name, metric.value)
        except Exception:
            logger.exception("Failed to emit StatsD metrics")

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            logger.exception("Failed to close StatsD client")


@dataclass(slots=True)
class Stopwatch:
    _clock: Clock
    _started_at: float

    @classmethod
    def start(cls, *, clock: Clock = time.monotonic) -> Stopwatch:
        return cls(_clock=clock, _started_at=clock())

    def elapsed_milliseconds(self) -> float:
        return (self._clock() - self._started_at) * 1_000


_metrics_sink: MetricsSink = NullMetricsSink()


def get_metrics_sink() -> MetricsSink:
    return _metrics_sink


def create_metrics_sink(
    environ: Mapping[str, str] | None = None,
    *,
    client_factory: Callable[..., _StatsDClient] = StatsClient,
) -> MetricsSink:
    environ = os.environ if environ is None else environ
    host = environ.get("STATSD_HOST", "").strip()
    if not host:
        logger.info("StatsD metrics are disabled because STATSD_HOST is not set")
        return NullMetricsSink()

    port = int(environ.get("STATSD_PORT", str(DEFAULT_STATSD_PORT)))
    if not 1 <= port <= 65_535:
        raise ValueError("STATSD_PORT is outside the range 1..65535")

    prefix = environ.get("STATSD_PREFIX", DEFAULT_STATSD_PREFIX).strip() or None
    client = client_factory(host=host, port=port, prefix=prefix)

    logger.info("StatsD metrics are enabled for %s:%d with prefix %s", host, port, prefix or "<none>")
    return StatsDMetricsSink(client)


@contextmanager
def configured_metrics(
    environ: Mapping[str, str] | None = None,
    *,
    client_factory: Callable[..., _StatsDClient] = StatsClient,
) -> Iterator[MetricsSink]:
    global _metrics_sink

    previous_sink = _metrics_sink
    sink = create_metrics_sink(environ, client_factory=client_factory)
    _metrics_sink = sink
    try:
        yield sink
    finally:
        _metrics_sink = previous_sink
        sink.close()

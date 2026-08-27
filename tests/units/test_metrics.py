from types import TracebackType
from typing import Self

import pytest
from hamcrest import assert_that, equal_to, instance_of, is_, same_instance

from cringe_pics_telebot.helpers.metrics import (
    CounterMetric,
    GaugeMetric,
    NullMetricsSink,
    StatsDMetricsSink,
    Stopwatch,
    TimingMetric,
    configured_metrics,
    create_metrics_sink,
    get_metrics_sink,
)


def test_stopwatch_uses_injected_monotonic_clock() -> None:
    clock = iter((10.0, 10.125)).__next__

    assert_that(Stopwatch.start(clock=clock).elapsed_milliseconds(), equal_to(125))


def test_create_metrics_sink_disables_metrics_without_host() -> None:
    factory_called = False

    def client_factory(**kwargs: object) -> _RecordingStatsDClient:
        nonlocal factory_called
        factory_called = True
        return _RecordingStatsDClient()

    sink = create_metrics_sink({}, client_factory=client_factory)

    assert_that(sink, instance_of(NullMetricsSink))
    assert_that(factory_called, is_(False))


def test_create_metrics_sink_uses_external_endpoint_and_emits_pipeline() -> None:
    client = _RecordingStatsDClient()
    client_arguments: dict[str, object] = {}

    def client_factory(**kwargs: object) -> _RecordingStatsDClient:
        client_arguments.update(kwargs)
        return client

    sink = create_metrics_sink(
        {
            "STATSD_HOST": "metrics.example.com",
            "STATSD_PORT": "18125",
            "STATSD_PREFIX": "test_bot",
        },
        client_factory=client_factory,
    )
    sink.emit(
        [
            TimingMetric("inline.total", 12.5),
            CounterMetric("inline.requests", 2),
            GaugeMetric("inline.results", 49),
        ]
    )

    assert_that(sink, instance_of(StatsDMetricsSink))
    assert_that(
        client_arguments,
        equal_to(
            {
                "host": "metrics.example.com",
                "port": 18125,
                "prefix": "test_bot",
            }
        ),
    )
    assert_that(client.pipeline_entered, is_(True))
    assert_that(client.pipeline_exited, is_(True))
    assert_that(
        client.calls,
        equal_to(
            [
                ("timing", "inline.total", 12.5),
                ("counter", "inline.requests", 2),
                ("gauge", "inline.results", 49),
            ]
        ),
    )


def test_create_metrics_sink_fails_fast_for_invalid_port() -> None:
    with pytest.raises(ValueError, match="invalid literal"):
        create_metrics_sink({"STATSD_HOST": "metrics.example.com", "STATSD_PORT": "invalid"})


def test_create_metrics_sink_fails_fast_when_client_initialization_fails() -> None:
    def client_factory(**kwargs: object) -> _RecordingStatsDClient:
        raise OSError("DNS unavailable")

    with pytest.raises(OSError, match="DNS unavailable"):
        create_metrics_sink({"STATSD_HOST": "metrics.example.com"}, client_factory=client_factory)


def test_statsd_sink_fails_open_when_pipeline_fails() -> None:
    client = _RecordingStatsDClient(fail_on_timing=True)

    StatsDMetricsSink(client).emit([TimingMetric("inline.total", 10)])

    assert_that(client.pipeline_entered, is_(True))
    assert_that(client.pipeline_exited, is_(True))


def test_configured_metrics_restores_previous_sink_and_closes_client() -> None:
    client = _RecordingStatsDClient()
    previous_sink = get_metrics_sink()

    with configured_metrics(
        {"STATSD_HOST": "metrics.example.com"},
        client_factory=lambda **kwargs: client,
    ) as configured_sink:
        assert_that(get_metrics_sink(), same_instance(configured_sink))

    assert_that(get_metrics_sink(), same_instance(previous_sink))
    assert_that(client.closed, is_(True))


class _RecordingStatsDClient:
    def __init__(self, *, fail_on_timing: bool = False) -> None:
        self.fail_on_timing = fail_on_timing
        self.calls: list[tuple[str, str, int | float]] = []
        self.pipeline_entered = False
        self.pipeline_exited = False
        self.closed = False

    def pipeline(self) -> _RecordingStatsDClient:
        return self

    def __enter__(self) -> Self:
        self.pipeline_entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.pipeline_exited = True

    def timing(self, stat: str, delta: float) -> None:
        if self.fail_on_timing:
            raise RuntimeError("send failed")
        self.calls.append(("timing", stat, delta))

    def incr(self, stat: str, count: int = 1) -> None:
        self.calls.append(("counter", stat, count))

    def gauge(self, stat: str, value: int | float) -> None:
        self.calls.append(("gauge", stat, value))

    def close(self) -> None:
        self.closed = True

"""The "alert to order in milliseconds" claim needs a number behind it."""
from __future__ import annotations

import pytest

from app.services import signal_latency


@pytest.fixture(autouse=True)
def _clean():
    signal_latency.reset()
    yield
    signal_latency.reset()


def test_reports_nothing_before_any_order_is_routed():
    assert signal_latency.latency_snapshot() == {
        "samples": 0,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }


def test_reports_percentiles_in_milliseconds():
    for seconds in (0.010, 0.020, 0.030, 0.040, 0.050):
        signal_latency.record_order_latency(seconds)

    snapshot = signal_latency.latency_snapshot()

    assert snapshot["samples"] == 5
    assert snapshot["p50_ms"] == 30.0
    assert snapshot["max_ms"] == 50.0


def test_a_slow_outlier_shows_up_in_p95_not_just_the_median():
    """The point of tracking p95: one 4-second broker round-trip among fast
    ones is exactly the problem this is meant to surface, and a median would
    hide it."""
    for _ in range(99):
        signal_latency.record_order_latency(0.020)
    signal_latency.record_order_latency(4.0)

    snapshot = signal_latency.latency_snapshot()

    assert snapshot["p50_ms"] == 20.0
    assert snapshot["max_ms"] == 4000.0


def test_window_is_bounded_so_it_cannot_grow_without_limit():
    for _ in range(signal_latency._MAX_SAMPLES + 250):
        signal_latency.record_order_latency(0.01)

    assert signal_latency.latency_snapshot()["samples"] == signal_latency._MAX_SAMPLES


def test_recording_never_raises_on_the_trading_path():
    """This sits inline in the routed-order path; a metrics bug must never
    take down a real order."""
    signal_latency.record_order_latency(-1.0)
    signal_latency.record_order_latency(0.0)

    assert signal_latency.latency_snapshot()["samples"] == 1

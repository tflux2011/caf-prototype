"""Tests for the live telemetry recorder."""

from caf.runtime.telemetry import Telemetry, _percentile


def test_percentile_edges():
    assert _percentile([], 50) == 0.0
    assert _percentile([7.0], 99) == 7.0
    assert _percentile([10.0, 20.0], 50) == 15.0
    assert _percentile([10.0, 20.0], 0) == 10.0
    assert _percentile([10.0, 20.0], 100) == 20.0


def test_records_requests_and_latency():
    tel = Telemetry("payment")
    for latency in (10.0, 20.0, 30.0, 40.0, 50.0):
        tel.record_request(latency, 200)
    snap = tel.snapshot()
    assert snap["service"] == "payment"
    assert snap["requests"] == 5
    assert snap["errors_5xx"] == 0
    assert snap["sample_count"] == 5
    assert snap["latency_p50_ms"] == 30.0
    assert snap["latency_p99_ms"] >= 49.0


def test_records_failure_categories_independently():
    tel = Telemetry("payment")
    tel.record_request(5.0, 503)
    tel.record_pool_timeout()
    tel.record_dependency_5xx()
    tel.record_dependency_5xx()
    tel.record_dependency_timeout()
    snap = tel.snapshot()
    assert snap["errors_5xx"] == 1
    assert snap["pool_timeout"] == 1
    assert snap["dependency_5xx"] == 2
    assert snap["dependency_timeout"] == 1

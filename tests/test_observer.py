"""Tests for deriving a fault signal from a live telemetry snapshot."""

from pathlib import Path

from caf.agent.signals import Symptom
from caf.runtime.observer import derive_signal
from caf.schema import RST

_RST = RST.model_validate_json((Path(__file__).resolve().parents[1] / "rst.json").read_text())


def _snap(**overrides) -> dict:
    base = {
        "service": "payment",
        "pool_timeout": 0,
        "dependency_timeout": 0,
        "dependency_5xx": 0,
        "latency_p99_ms": 0.0,
        "sample_count": 0,
    }
    base.update(overrides)
    return base


def test_healthy_snapshot_yields_no_signal():
    assert derive_signal(_snap(), "payment", _RST) is None


def test_pool_timeout_takes_priority():
    signal = derive_signal(_snap(pool_timeout=3, dependency_5xx=2), "payment", _RST)
    assert signal is not None
    assert signal.symptom == Symptom.pool_timeout
    assert signal.evidence["pool_timeouts"] == 3


def test_dependency_5xx_maps_through():
    signal = derive_signal(_snap(dependency_5xx=4), "subscription", _RST)
    assert signal is not None
    assert signal.symptom == Symptom.dependency_5xx


def test_latency_spike_uses_declared_envelope():
    # payment declares p99=250ms; 3x that with enough samples is a spike.
    signal = derive_signal(_snap(latency_p99_ms=1000.0, sample_count=10), "payment", _RST)
    assert signal is not None
    assert signal.symptom == Symptom.latency_spike


def test_latency_spike_needs_enough_samples():
    assert derive_signal(_snap(latency_p99_ms=1000.0, sample_count=2), "payment", _RST) is None

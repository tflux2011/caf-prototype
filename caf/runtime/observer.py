"""Turn a live telemetry snapshot into a fault signal.

This is the bridge from *real observations* to the symptom vocabulary the
Tier-2 reasoner already understands. The mapping is a threshold heuristic --
labelled as such -- and deliberately conservative: it reports the single most
specific symptom present, in priority order, or nothing when the service looks
healthy.

Note what this function does NOT get: ground truth. A running agent never knows
the true root cause; it only sees what its own service measured. That is the
honest condition the whole architecture is meant to operate under.
"""

from __future__ import annotations

from typing import Optional

from caf.agent.signals import FaultSignal, Symptom
from caf.schema import RST

# A latency spike is declared when observed p99 exceeds the service's declared
# envelope by this factor, and only once enough samples exist to be meaningful.
LATENCY_SPIKE_FACTOR = 3.0
MIN_LATENCY_SAMPLES = 5


def derive_signal(snapshot: dict, service: str, rst: RST) -> Optional[FaultSignal]:
    """Map a telemetry snapshot to at most one :class:`FaultSignal`."""
    pool_timeout = int(snapshot.get("pool_timeout", 0))
    dependency_timeout = int(snapshot.get("dependency_timeout", 0))
    dependency_5xx = int(snapshot.get("dependency_5xx", 0))

    if pool_timeout > 0:
        return FaultSignal(
            service=service,
            symptom=Symptom.pool_timeout,
            evidence={"pool_timeouts": pool_timeout},
        )
    if dependency_timeout > 0:
        return FaultSignal(
            service=service,
            symptom=Symptom.dependency_timeout,
            evidence={"dependency_timeouts": dependency_timeout},
        )
    if dependency_5xx > 0:
        return FaultSignal(
            service=service,
            symptom=Symptom.dependency_5xx,
            evidence={"dependency_5xx": dependency_5xx},
        )

    node = rst.nodes.get(service)
    p99 = float(snapshot.get("latency_p99_ms", 0.0))
    samples = int(snapshot.get("sample_count", 0))
    if node is not None and node.expected_latency_ms is not None and samples >= MIN_LATENCY_SAMPLES:
        threshold = node.expected_latency_ms.p99 * LATENCY_SPIKE_FACTOR
        if p99 > threshold:
            return FaultSignal(
                service=service,
                symptom=Symptom.latency_spike,
                evidence={"p99_ms": int(p99), "threshold_ms": int(threshold)},
            )
    return None

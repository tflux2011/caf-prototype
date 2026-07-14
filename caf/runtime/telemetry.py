"""In-process telemetry recorder for a running service.

This records *real* observations only -- request latency, response status, and
the dependency/pool failures the handlers actually hit. It derives nothing and
makes no judgements; turning these raw counters into a fault symptom is the
observer's job (:mod:`caf.runtime.observer`). Keeping recording and
interpretation separate is deliberate: the numbers here are ground-level facts,
not heuristics.

Thread-safe because Starlette runs sync path operations in a worker threadpool.
"""

from __future__ import annotations

import threading
import time
from collections import deque


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


class Telemetry:
    """Live counters and a latency window for one service instance."""

    def __init__(self, service: str, *, window: int = 256) -> None:
        self.service = service
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=window)
        self._start = time.monotonic()
        self.requests = 0
        self.errors_5xx = 0
        self.dependency_5xx = 0
        self.dependency_timeout = 0
        self.pool_timeout = 0

    def record_request(self, latency_ms: float, status: int) -> None:
        with self._lock:
            self.requests += 1
            self._latencies.append(latency_ms)
            if status >= 500:
                self.errors_5xx += 1

    def record_dependency_5xx(self) -> None:
        with self._lock:
            self.dependency_5xx += 1

    def record_dependency_timeout(self) -> None:
        with self._lock:
            self.dependency_timeout += 1

    def record_pool_timeout(self) -> None:
        with self._lock:
            self.pool_timeout += 1

    def snapshot(self) -> dict:
        """A point-in-time view of the recorded facts (JSON-serialisable)."""
        with self._lock:
            samples = sorted(self._latencies)
            return {
                "service": self.service,
                "uptime_s": round(time.monotonic() - self._start, 1),
                "requests": self.requests,
                "errors_5xx": self.errors_5xx,
                "dependency_5xx": self.dependency_5xx,
                "dependency_timeout": self.dependency_timeout,
                "pool_timeout": self.pool_timeout,
                "latency_p50_ms": round(_percentile(samples, 50), 2),
                "latency_p99_ms": round(_percentile(samples, 99), 2),
                "sample_count": len(samples),
            }

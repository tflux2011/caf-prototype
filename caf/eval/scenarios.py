"""Fault scenarios for the RQ1 comparison, over the subscription testbed.

Each scenario pairs a fault signal (what a service observes) with the node that
is *actually* at fault (ground truth). The mix is deliberate: some faults are
local to the observing service, and some originate downstream or at an external
dependency — the cases where topology-blind reasoning mislocalizes.
"""

from __future__ import annotations

from caf.agent.signals import FaultSignal, Symptom


def scenarios() -> list[FaultSignal]:
    """The RQ1 fault set. ``true_root_cause`` is scoring ground truth only."""

    return [
        # 1. Local pool exhaustion at payment. Both arms should get this: the
        #    subject is the observing service.
        FaultSignal(
            service="payment",
            symptom=Symptom.pool_timeout,
            evidence={"pool_timeouts": 7},
            true_root_cause="payment",
        ),
        # 2. Postgres outage seen at payment as timeouts. The true subject is an
        #    external dependency (alpha = empty): grounded escalates, ungrounded
        #    blames and "restarts" payment.
        FaultSignal(
            service="payment",
            symptom=Symptom.dependency_timeout,
            evidence={"downstream_timeouts": 9},
            true_root_cause="postgres-primary",
        ),
        # 3. Stripe (external, non-idempotent) returning 5xx, seen at payment.
        FaultSignal(
            service="payment",
            symptom=Symptom.dependency_5xx,
            evidence={"downstream_5xx": 12},
            true_root_cause="stripe",
        ),
        # 4. Payment slowness surfaces as a retry storm / timeouts at the caller
        #    (subscription). Grounded follows the edge to payment; ungrounded
        #    blames subscription.
        FaultSignal(
            service="subscription",
            symptom=Symptom.dependency_timeout,
            evidence={"downstream_timeouts": 5},
            true_root_cause="payment",
        ),
        # 5. Auth degraded as seen by user-api.
        FaultSignal(
            service="user-api",
            symptom=Symptom.dependency_5xx,
            evidence={"downstream_5xx": 4},
            true_root_cause="auth",
        ),
        # 6. Local latency spike at subscription (subject is the observer).
        FaultSignal(
            service="subscription",
            symptom=Symptom.latency_spike,
            evidence={"p99_ms": 900},
            true_root_cause="subscription",
        ),
    ]

"""Payment service for the subscription testbed.

This is the critical node of Listing 1: it depends on Postgres (SQL) and a
Stripe-like external HTTP API, and declares the full permitted-action registry.
"""

from caf.annotations import caf_service
from caf.runtime.instrument import attach_telemetry
from fastapi import FastAPI, HTTPException
import httpx
import psycopg

# Code-derived dependency endpoints. The compiler resolves the hostnames
# ("postgres-primary", "stripe") from these constants.
DB_DSN = "postgresql://billing@postgres-primary:5432/billing"
STRIPE_URL = "http://stripe:9000"

app = FastAPI(title="payment")
telemetry = attach_telemetry(app, "payment")
stripe_client = httpx.Client(timeout=5.0)


@caf_service(
    name="payment",
    failure_domain="billing",
    criticality="critical",
    ownership="team-payments",
    permitted_actions=[
        "apply_traffic_throttle",
        "restart_connection_pool",
        "trigger_canary_rollback",
        "open_circuit_breaker",
    ],
    retry={"max": 3, "backoff_ms": 200, "jitter": True},
    expected_latency_ms={"p50": 40, "p99": 250},
    external_deps={
        "postgres-primary": {
            "failure_domain": "data-tier",
            "criticality": "critical",
            "pool": "billing-pool",
            "timeout_ms": 500,
        },
    },
)
class PaymentService:
    """CAF descriptor for the payment service."""


def _touch_db() -> None:
    """Real Postgres round-trip. A per-request ``psycopg.connect`` keeps the
    extractor's SQL-edge signal intact and lets later phases inject pool
    exhaustion against the ``billing-pool`` this DSN represents."""
    conn = psycopg.connect(DB_DSN, connect_timeout=2)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        conn.close()


@app.post("/charge")
def charge() -> dict:
    try:
        _touch_db()
    except psycopg.Error as exc:
        telemetry.record_pool_timeout()
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    try:
        response = stripe_client.post(f"{STRIPE_URL}/v1/charges")
    except httpx.HTTPError as exc:
        telemetry.record_dependency_timeout()
        raise HTTPException(status_code=502, detail="payment processor unavailable") from exc
    if response.status_code >= 500:
        telemetry.record_dependency_5xx()
        raise HTTPException(status_code=502, detail="payment processor unavailable")
    return {"charged": True}


@app.post("/refund")
def refund() -> dict:
    try:
        _touch_db()
    except psycopg.Error as exc:
        telemetry.record_pool_timeout()
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    try:
        response = stripe_client.post(f"{STRIPE_URL}/v1/refunds")
    except httpx.HTTPError as exc:
        telemetry.record_dependency_timeout()
        raise HTTPException(status_code=502, detail="payment processor unavailable") from exc
    if response.status_code >= 500:
        telemetry.record_dependency_5xx()
        raise HTTPException(status_code=502, detail="payment processor unavailable")
    return {"refunded": True}

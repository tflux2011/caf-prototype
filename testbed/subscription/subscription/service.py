"""Subscription service for the subscription testbed."""

from caf.annotations import caf_service
from caf.runtime.instrument import attach_telemetry
from fastapi import FastAPI, HTTPException
import httpx

PAYMENT_URL = "http://payment:8080"

app = FastAPI(title="subscription")
telemetry = attach_telemetry(app, "subscription")
client = httpx.Client(timeout=5.0)


@caf_service(
    name="subscription",
    failure_domain="billing",
    criticality="high",
    ownership="team-billing",
    permitted_actions=["apply_traffic_throttle", "open_circuit_breaker"],
    expected_latency_ms={"p50": 30, "p99": 180},
)
class SubscriptionService:
    """CAF descriptor for the subscription service."""


@app.get("/status")
def status() -> dict:
    try:
        response = client.post(f"{PAYMENT_URL}/charge")
    except httpx.HTTPError as exc:
        telemetry.record_dependency_timeout()
        raise HTTPException(status_code=502, detail="upstream payment unavailable") from exc
    if response.status_code >= 500:
        telemetry.record_dependency_5xx()
        raise HTTPException(status_code=502, detail="upstream payment unavailable")
    return {"active": True, "payment": response.json()}

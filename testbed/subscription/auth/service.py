"""Identity/auth service for the subscription testbed."""

from caf.annotations import caf_service
from caf.runtime.instrument import attach_telemetry
from fastapi import FastAPI, HTTPException
import httpx

SUBSCRIPTION_URL = "http://subscription:8080"

app = FastAPI(title="auth")
telemetry = attach_telemetry(app, "auth")
client = httpx.Client(timeout=5.0)


@caf_service(
    name="auth",
    failure_domain="identity",
    criticality="high",
    ownership="team-identity",
    permitted_actions=["restart_connection_pool", "open_circuit_breaker"],
    expected_latency_ms={"p50": 15, "p99": 90},
)
class AuthService:
    """CAF descriptor for the authentication service."""


@app.post("/verify")
def verify() -> dict:
    try:
        response = client.get(f"{SUBSCRIPTION_URL}/status")
    except httpx.HTTPError as exc:
        telemetry.record_dependency_timeout()
        raise HTTPException(status_code=502, detail="upstream subscription unavailable") from exc
    if response.status_code >= 500:
        telemetry.record_dependency_5xx()
        raise HTTPException(status_code=502, detail="upstream subscription unavailable")
    return {"verified": True, "subscription": response.json()}

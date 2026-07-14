"""Public API gateway for the subscription testbed.

Entry point of the call chain: User API -> Auth -> Subscription -> Payment.
This module is parsed statically by the RST compiler; it does not need its
runtime dependencies installed for extraction.
"""

from caf.annotations import caf_service
from caf.runtime.instrument import attach_telemetry
from fastapi import FastAPI, HTTPException
import httpx

AUTH_URL = "http://auth:8080"

app = FastAPI(title="user-api")
telemetry = attach_telemetry(app, "user-api")
# Reused HTTP client. The extractor recognises this as an httpx client and
# resolves the call sites below into ``http`` edges to the ``auth`` node.
client = httpx.Client(timeout=5.0)


@caf_service(
    name="user-api",
    failure_domain="edge",
    criticality="high",
    ownership="team-gateway",
    permitted_actions=["apply_traffic_throttle", "open_circuit_breaker"],
    expected_latency_ms={"p50": 20, "p99": 120},
)
class UserApiService:
    """CAF descriptor for the public API gateway."""


@app.post("/signup")
def signup() -> dict:
    try:
        response = client.post(f"{AUTH_URL}/verify")
    except httpx.HTTPError as exc:
        telemetry.record_dependency_timeout()
        raise HTTPException(status_code=502, detail="upstream auth unavailable") from exc
    if response.status_code >= 500:
        telemetry.record_dependency_5xx()
        raise HTTPException(status_code=502, detail="upstream auth unavailable")
    return {"ok": True, "auth": response.json()}


@app.get("/me")
def me() -> dict:
    try:
        response = client.post(f"{AUTH_URL}/verify")
    except httpx.HTTPError as exc:
        telemetry.record_dependency_timeout()
        raise HTTPException(status_code=502, detail="upstream auth unavailable") from exc
    if response.status_code >= 500:
        telemetry.record_dependency_5xx()
        raise HTTPException(status_code=502, detail="upstream auth unavailable")
    return {"user": "demo", "auth": response.json()}

"""Minimal Stripe-like stub for the testbed.

Stands in for the external payment processor so the ``payment`` service has a
real HTTP dependency to call. It carries NO ``@caf_service`` annotation, so the
RST compiler correctly classifies ``stripe`` as an external dependency with an
empty (closed) permitted-action set.

For Phase C it also exposes a fault-injection control plane (``/admin/fault``)
so the harness can make this external dependency misbehave -- return 5xx or add
latency -- WITHOUT touching any instrumented CAF service. Keeping fault control
on the stub alone means the static RST extractor never sees a new signal: the
real services' source is unchanged.

Security: the control plane mutates only this stub's own behaviour, carries no
secrets, and is bound to localhost by the compose port mapping. It is a testbed
device only. Never point this at real Stripe or real credentials.
"""

import time
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="stripe-stub")

FaultMode = Literal["none", "error500", "latency"]


class FaultState(BaseModel):
    """Current injected-fault configuration for the stub."""

    model_config = {"extra": "forbid"}

    mode: FaultMode = "none"
    latency_ms: int = 0


_fault = FaultState()


def _apply_fault() -> None:
    """Honour the currently configured fault before serving a response."""
    if _fault.mode == "latency" and _fault.latency_ms > 0:
        time.sleep(_fault.latency_ms / 1000.0)
    if _fault.mode == "error500":
        raise HTTPException(status_code=500, detail="stripe fault injected")


@app.post("/admin/fault")
def set_fault(state: FaultState) -> FaultState:
    """Set (or clear) the injected fault. Testbed-only control plane."""
    global _fault
    if state.latency_ms < 0 or state.latency_ms > 60_000:
        raise HTTPException(status_code=422, detail="latency_ms out of range")
    _fault = state
    return _fault


@app.get("/admin/fault")
def get_fault() -> FaultState:
    """Report the currently injected fault."""
    return _fault


@app.post("/v1/charges")
def charges() -> dict:
    _apply_fault()
    return {"id": "ch_test", "status": "succeeded"}


@app.post("/v1/refunds")
def refunds() -> dict:
    _apply_fault()
    return {"id": "re_test", "status": "succeeded"}

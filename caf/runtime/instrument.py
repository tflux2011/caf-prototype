"""Attach live telemetry to a running service without touching its RST shape.

A service calls :func:`attach_telemetry` right after building its FastAPI app.
This installs latency/status middleware and publishes a ``/caf/telemetry``
snapshot endpoint.

Crucially, the endpoint is registered with ``app.add_api_route`` -- a plain
function call, *not* an ``@app.get`` decorator. The RST extractor only reads
route *decorators*, so this operational endpoint is invisible to it and the
compiled ``rst.json`` is unchanged.
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request

from .telemetry import Telemetry


def attach_telemetry(app: FastAPI, service: str) -> Telemetry:
    """Wire request-timing middleware and a telemetry endpoint onto ``app``."""
    telemetry = Telemetry(service)

    @app.middleware("http")
    async def _record(request: Request, call_next):  # noqa: ANN001, ANN202
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        # Do not let the telemetry endpoint measure itself.
        if request.url.path != "/caf/telemetry":
            telemetry.record_request(elapsed_ms, response.status_code)
        return response

    def _telemetry_snapshot() -> dict:
        return telemetry.snapshot()

    # Registered as a call, not a decorator: invisible to the RST extractor.
    app.add_api_route("/caf/telemetry", _telemetry_snapshot, methods=["GET"])
    return telemetry

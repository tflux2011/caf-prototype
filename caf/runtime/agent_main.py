"""A real CAF sidecar agent (Phase B).

One of these runs alongside each service. On a fixed interval it:

1. scrapes its local service's ``/caf/telemetry`` (real observation);
2. derives a fault signal (or nothing) via :mod:`caf.runtime.observer`;
3. forms a grounded belief and runs node-local triage (Algorithm 2);
4. gossips its belief store to a random fan-out of peers over real HTTP.

Beliefs arriving from peers are ingested through the same fusion path used in
simulation, so a subject is classified *systemic* only when a quorum of
distinct origins corroborate it. The difference from the in-process simulation
is that the beliefs now genuinely cross the network.

The reasoner behind triage is selectable (Phase D). By default it is the
deterministic grounded playbook; setting ``CAF_AGENT_REASONER=openai`` (with an
``OPENAI_API_KEY`` in the environment) makes the *running* stack diagnose with a
real model over the network. Whichever reasoner is used, the same in-code
admission boundary (Theorem 1) still gates every applied repair.

Configuration is entirely via environment variables so the same image serves
every agent; nothing sensitive is baked in. The API key, when used, is read from
the environment only -- never logged or written to disk.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI

from caf.agent.local_agent import Diagnoser, triage
from caf.agent.signals import Diagnosis, FaultSignal
from caf.fabric.belief import Belief
from caf.fabric.node import NodeAgent
from caf.reason import OpenAIReasoner
from caf.runtime.observer import derive_signal
from caf.schema import RST

AGENT_ID = os.environ.get("AGENT_ID", "agent-0")
SERVICE = os.environ.get("AGENT_SERVICE", "unknown")
TELEMETRY_URL = os.environ.get("SERVICE_TELEMETRY_URL", "")
PEERS = [p.strip() for p in os.environ.get("AGENT_PEERS", "").split(",") if p.strip()]
RST_PATH = os.environ.get("RST_PATH", "rst.json")
INTERVAL_S = float(os.environ.get("AGENT_INTERVAL_S", "2"))
FANOUT = int(os.environ.get("AGENT_FANOUT", "2"))
SEED = int(os.environ.get("AGENT_SEED", "1337"))
HTTP_TIMEOUT_S = float(os.environ.get("AGENT_HTTP_TIMEOUT_S", "2"))
# Phase D: which reasoner backs triage. "deterministic" (default) or "openai".
REASONER_KIND = os.environ.get("CAF_AGENT_REASONER", "deterministic").lower()
GROUNDED = os.environ.get("CAF_AGENT_GROUNDED", "1").lower() not in ("0", "false", "no")


def _load_rst() -> RST:
    return RST.model_validate_json(Path(RST_PATH).read_text(encoding="utf-8"))


rst = _load_rst()
_domain = rst.nodes[SERVICE].failure_domain if SERVICE in rst.nodes else None
node = NodeAgent(AGENT_ID, SERVICE, _domain, rst)
_rng = random.Random(SEED)
_events: list[dict] = []
_client: Optional[httpx.AsyncClient] = None

# Phase D: build the triage reasoner. Deterministic by default; a real OpenAI
# model is used only when explicitly requested (and requires OPENAI_API_KEY).
_reasoner: Optional[OpenAIReasoner] = None
_diagnoser: Optional[Diagnoser] = None
if REASONER_KIND == "openai":
    _reasoner = OpenAIReasoner()  # reads OPENAI_API_KEY / CAF_OPENAI_MODEL

    def _diagnoser(signal: FaultSignal, model: RST) -> Diagnosis:
        assert _reasoner is not None
        return _reasoner.diagnose(signal, model, grounded=GROUNDED)


async def _scrape() -> Optional[dict]:
    if not TELEMETRY_URL or _client is None:
        return None
    try:
        response = await _client.get(TELEMETRY_URL, timeout=HTTP_TIMEOUT_S)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None


def _select_peers() -> list[str]:
    if len(PEERS) <= FANOUT:
        return list(PEERS)
    return _rng.sample(PEERS, FANOUT)


async def _push(beliefs: list[Belief]) -> None:
    if _client is None or not beliefs:
        return
    for peer in _select_peers():
        for belief in beliefs:
            try:
                await _client.post(
                    f"http://{peer}/gossip", json=belief.model_dump(), timeout=HTTP_TIMEOUT_S
                )
            except httpx.HTTPError:
                pass  # gossip is best-effort; anti-entropy retries next round


async def _tick() -> None:
    snapshot = await _scrape()
    if snapshot is not None:
        signal = derive_signal(snapshot, SERVICE, rst)
        if signal is not None:
            belief = node.form_belief(signal, epoch=0)
            if node.ingest(belief):  # only act on a genuinely new local belief
                # triage may call a real model over the network, so run it off
                # the event loop; a reasoner hiccup must not stop gossip.
                try:
                    result = await asyncio.to_thread(
                        triage, signal, rst, grounded=GROUNDED, diagnoser=_diagnoser
                    )
                except Exception as exc:  # noqa: BLE001 - record and keep going
                    _events.append(
                        {
                            "t": round(time.time(), 3),
                            "symptom": signal.symptom.value,
                            "error": type(exc).__name__,
                            "phase": "triage",
                        }
                    )
                else:
                    _events.append(
                        {
                            "t": round(time.time(), 3),
                            "symptom": signal.symptom.value,
                            "subject": result.diagnosis.suspected_subject,
                            "disposition": result.disposition,
                            "action": result.repair.action if result.repair else None,
                        }
                    )
    await _push(node.digest())


async def _loop() -> None:
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a sidecar must not crash the agent
            _events.append({"t": round(time.time(), 3), "error": type(exc).__name__})
        await asyncio.sleep(INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    global _client
    _client = httpx.AsyncClient()
    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _client.aclose()
        _client = None


app = FastAPI(title=f"caf-agent-{SERVICE}", lifespan=lifespan)


@app.post("/gossip")
async def gossip(belief: Belief) -> dict:
    """Ingest a peer's belief. Returns whether it was new (idempotency)."""
    return {"new": node.ingest(belief)}


@app.get("/state")
async def state() -> dict:
    """Observable snapshot of this agent's beliefs and classifications."""
    reasoner = {"kind": REASONER_KIND, "grounded": GROUNDED}
    if _reasoner is not None:
        reasoner.update(
            {
                "model": _reasoner.model,
                "prompt_tokens": _reasoner.total_prompt_tokens,
                "completion_tokens": _reasoner.total_completion_tokens,
            }
        )
    return {
        "agent": AGENT_ID,
        "service": SERVICE,
        "domain": _domain,
        "peers": PEERS,
        "reasoner": reasoner,
        "subjects": {
            subject: {
                "fused": round(node.fused[subject], 3),
                "reporters": sorted(node.reporters.get(subject, set())),
                "classification": node.classify(subject),
                "mechanisms": sorted(node.mechanisms(subject)),
                "mechanism_divergent": node.mechanism_divergent(subject),
            }
            for subject in sorted(node.fused)
        },
        "events": _events[-20:],
    }

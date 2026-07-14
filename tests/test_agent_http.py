"""Tests for the sidecar agent's real gossip endpoint (HTTP transport).

These exercise the actual FastAPI routes an agent exposes: a peer's belief is
serialised, POSTed over HTTP, ingested through the fusion path, and reflected in
the agent's classification -- the same contract that crosses the network in the
running testbed.
"""

import os
from pathlib import Path

os.environ.setdefault("RST_PATH", str(Path(__file__).resolve().parents[1] / "rst.json"))
os.environ.setdefault("AGENT_ID", "test-agent")
os.environ.setdefault("AGENT_SERVICE", "payment")
os.environ.setdefault("AGENT_PEERS", "")

from fastapi.testclient import TestClient  # noqa: E402

from caf.fabric.belief import Belief  # noqa: E402
from caf.runtime import agent_main  # noqa: E402


def _belief(origin: str, subject: str, confidence: float) -> dict:
    return Belief(
        origin=origin,
        artifact_version=agent_main.rst.version,
        subject=subject,
        hypothesis=f"{subject} is the root cause",
        confidence=confidence,
        epoch=0,
    ).model_dump()


def test_gossip_ingests_and_is_idempotent():
    client = TestClient(agent_main.app)
    payload = _belief("auth-1", "postgres-primary", 0.8)

    first = client.post("/gossip", json=payload)
    assert first.status_code == 200
    assert first.json()["new"] is True

    # Anti-entropy re-delivery of the same belief must not double-count.
    second = client.post("/gossip", json=payload)
    assert second.json()["new"] is False


def test_quorum_of_distinct_origins_classifies_systemic():
    client = TestClient(agent_main.app)
    subject = "payment"

    client.post("/gossip", json=_belief("payment-1", subject, 0.9))
    state = client.get("/state").json()
    assert state["subjects"][subject]["classification"] == "isolated"

    # A second distinct origin corroborating the same subject reaches quorum.
    client.post("/gossip", json=_belief("subscription-1", subject, 0.9))
    state = client.get("/state").json()
    entry = state["subjects"][subject]
    assert set(entry["reporters"]) == {"payment-1", "subscription-1"}
    assert entry["classification"] == "systemic"

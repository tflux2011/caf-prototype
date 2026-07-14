"""Tests for the gossip fabric: idempotency, corroboration, and classification."""

from pathlib import Path

from caf.agent.signals import FaultSignal, Symptom
from caf.fabric.belief import Belief
from caf.fabric.node import NodeAgent
from caf.fabric.gossip import Fabric
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

TESTBED = Path(__file__).resolve().parents[1] / "testbed" / "subscription"


def _rst() -> RST:
    return compile_rst(TESTBED, version="0.1.0", generated_by="pytest")


def _cluster(rst: RST, service: str, n: int) -> list[NodeAgent]:
    node = rst.nodes[service]
    return [NodeAgent(f"{service}-{i}", service, node.failure_domain, rst) for i in range(n)]


def test_duplicate_belief_delivery_is_idempotent():
    rst = _rst()
    agent = _cluster(rst, "payment", 1)[0]
    belief = Belief(
        origin="payment-9", artifact_version="0.1.0", subject="postgres-primary",
        hypothesis="down", confidence=0.9,
    )
    assert agent.ingest(belief) is True
    first = agent.fused["postgres-primary"]
    # Re-delivery of the identical key must not change the estimate.
    assert agent.ingest(belief) is False
    assert agent.fused["postgres-primary"] == first
    assert len(agent.reporters["postgres-primary"]) == 1


def test_single_node_cannot_reach_systemic_alone():
    rst = _rst()
    agent = _cluster(rst, "payment", 1)[0]
    agent.observe(FaultSignal(service="payment", symptom=Symptom.dependency_timeout), epoch=0)
    # One confident reporter is still one reporter: below quorum -> isolated.
    assert agent.classify("postgres-primary") == "isolated"


def test_gossip_reaches_systemic_consensus_across_replicas():
    rst = _rst()
    cluster = _cluster(rst, "payment", 3)
    fabric = Fabric(cluster, fanout=2, gossip=True)
    for node in cluster:
        node.observe(FaultSignal(service="payment", symptom=Symptom.dependency_timeout), epoch=0)
    fabric.run()
    # Every replica now sees three corroborating origins -> systemic.
    for node in cluster:
        assert node.classify("postgres-primary") == "systemic"
        assert len(node.reporters["postgres-primary"]) == 3


def test_divergent_mechanisms_on_same_subject_still_reach_systemic():
    # Regression for a real-run finding: two nodes localize the same subject
    # (payment) but from different sides -- one sees pool exhaustion, the other
    # sees the downstream 5xx. That is agreement on *where*, not a contradiction,
    # so it must corroborate to systemic and be flagged as mechanism-divergent.
    rst = _rst()
    agent = _cluster(rst, "payment", 1)[0]
    root = Belief(
        origin="payment-1", artifact_version="0.1.0", subject="payment",
        hypothesis="connection pool exhausted", confidence=0.8,
    )
    upstream = Belief(
        origin="subscription-1", artifact_version="0.1.0", subject="payment",
        hypothesis="downstream returning 5xx", confidence=0.8,
    )
    assert agent.ingest(root) is True
    assert agent.ingest(upstream) is True
    assert agent.fused["payment"] >= agent.theta_cons  # not dragged below threshold
    assert len(agent.reporters["payment"]) == 2
    assert agent.classify("payment") == "systemic"
    assert agent.mechanism_divergent("payment") is True
    assert agent.mechanisms("payment") == {
        "connection pool exhausted",
        "downstream returning 5xx",
    }


def test_isolated_fault_stays_isolated_under_gossip():
    rst = _rst()
    cluster = _cluster(rst, "payment", 3)
    fabric = Fabric(cluster, fanout=2, gossip=True)
    # Only one replica has a local pool fault.
    cluster[0].observe(FaultSignal(service="payment", symptom=Symptom.pool_timeout), epoch=0)
    fabric.run()
    assert cluster[0].classify("payment") == "isolated"


def test_no_gossip_baseline_misses_systemic():
    rst = _rst()
    cluster = _cluster(rst, "payment", 3)
    fabric = Fabric(cluster, fanout=2, gossip=False)
    for node in cluster:
        node.observe(FaultSignal(service="payment", symptom=Symptom.dependency_timeout), epoch=0)
    fabric.run()
    for node in cluster:
        assert node.classify("postgres-primary") == "isolated"  # cannot tell, alone


def test_dissemination_completes_and_is_bounded():
    rst = _rst()
    cluster = _cluster(rst, "payment", 8)
    fabric = Fabric(cluster, fanout=2, gossip=True)
    cluster[0].observe(FaultSignal(service="payment", symptom=Symptom.dependency_timeout), epoch=0)
    rounds = fabric.run()
    assert fabric.coverage("postgres-primary") == 8  # reached everyone
    assert rounds <= 8  # logarithmic-ish, well under N

"""RQ2: does belief gossip improve isolated-vs-systemic classification?

We build a replicated cluster over the subscription testbed (several agent
instances per service, sharing a failure domain), inject a balanced mix of
isolated and systemic faults, and compare two arms:

* gossip on  -- nodes exchange and fuse beliefs, then classify;
* gossip off -- each node sees only its own observation.

The scored property is per-observation classification accuracy. The result is
structural: a lone node cannot tell "just me" from "everyone", so without gossip
every systemic fault is misclassified as isolated. This is the mechanism RQ2
asks about, not a calibrated detector benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from caf.agent.signals import FaultSignal, Symptom
from caf.fabric.node import NodeAgent
from caf.fabric.gossip import Fabric
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

TESTBED = Path(__file__).resolve().parents[2] / "testbed" / "subscription"

REPLICAS = 3  # instances per service
SERVICES = ["user-api", "auth", "subscription", "payment"]


@dataclass
class Scenario:
    name: str
    label: str  # ground-truth: "isolated" or "systemic"
    # observations: (service, replica_index, symptom)
    observations: list[tuple[str, int, Symptom]]


def _build_cluster(rst: RST) -> list[NodeAgent]:
    nodes: list[NodeAgent] = []
    for service in SERVICES:
        node = rst.nodes.get(service)
        domain = node.failure_domain if node else None
        for i in range(REPLICAS):
            nodes.append(NodeAgent(f"{service}-{i}", service, domain, rst))
    return nodes


def _scenarios() -> list[Scenario]:
    return [
        Scenario(
            "isolated pool exhaustion (1 payment replica)",
            "isolated",
            [("payment", 0, Symptom.pool_timeout)],
        ),
        Scenario(
            "systemic postgres saturation (all payment replicas)",
            "systemic",
            [("payment", i, Symptom.dependency_timeout) for i in range(REPLICAS)],
        ),
        Scenario(
            "isolated latency spike (1 auth replica)",
            "isolated",
            [("auth", 1, Symptom.latency_spike)],
        ),
        Scenario(
            "systemic auth outage (all user-api replicas)",
            "systemic",
            [("user-api", i, Symptom.dependency_5xx) for i in range(REPLICAS)],
        ),
    ]


def _run_scenario(rst: RST, scenario: Scenario, *, gossip: bool, epoch: int):
    cluster = _build_cluster(rst)
    fabric = Fabric(cluster, fanout=2, gossip=gossip)

    observers: list[tuple[NodeAgent, str]] = []  # (node, subject it reported)
    for service, idx, symptom in scenario.observations:
        node = fabric.node(f"{service}-{idx}")
        signal = FaultSignal(service=service, symptom=symptom)
        belief = node.observe(signal, epoch=epoch)
        observers.append((node, belief.subject))

    fabric.run()

    # Each observing node classifies the subject it reported.
    predictions = [node.classify(subject) for node, subject in observers]
    correct = sum(1 for p in predictions if p == scenario.label)
    return correct, len(predictions)


def _dissemination_probe(rst: RST, sizes: list[int], fanout: int) -> list[tuple[int, int]]:
    """Rounds for one belief to reach all N replicas of a single domain.

    Randomized (seeded) peer selection is the epidemic model under which the
    paper's O(log N) dissemination bound holds; this probe reports the measured
    rounds so the growth is visible rather than merely asserted.
    """

    node = rst.nodes["payment"]
    out: list[tuple[int, int]] = []
    for n in sizes:
        cluster = [
            NodeAgent(f"payment-{i}", "payment", node.failure_domain, rst) for i in range(n)
        ]
        fabric = Fabric(cluster, fanout=fanout, gossip=True)
        cluster[0].observe(
            FaultSignal(service="payment", symptom=Symptom.dependency_timeout), epoch=0
        )
        rounds = fabric.run()
        assert fabric.coverage("postgres-primary") == n  # reached everyone
        out.append((n, rounds))
    return out


def run() -> int:
    rst = compile_rst(TESTBED, version="0.1.0", generated_by="caf-eval-rq2")
    scenarios = _scenarios()

    print("RQ2 - belief gossip vs no gossip (isolated vs systemic)")
    print(f"cluster: {len(SERVICES)} services x {REPLICAS} replicas = {len(SERVICES) * REPLICAS} nodes\n")

    header = f"{'scenario':46} {'truth':9} {'gossip':>10} {'no-gossip':>12}"
    print(header)
    print("-" * len(header))

    totals = {"gossip": [0, 0], "nogossip": [0, 0]}
    for epoch, sc in enumerate(scenarios):
        g_correct, g_n = _run_scenario(rst, sc, gossip=True, epoch=epoch)
        u_correct, u_n = _run_scenario(rst, sc, gossip=False, epoch=epoch)
        totals["gossip"][0] += g_correct
        totals["gossip"][1] += g_n
        totals["nogossip"][0] += u_correct
        totals["nogossip"][1] += u_n
        print(
            f"{sc.name:46} {sc.label:9} {f'{g_correct}/{g_n}':>10} {f'{u_correct}/{u_n}':>12}"
        )

    g = totals["gossip"]
    u = totals["nogossip"]
    print("\nsummary (correct classifications, higher is better):")
    print(f"  gossip on : {g[0]}/{g[1]}")
    print(f"  gossip off: {u[0]}/{u[1]}")

    probe = _dissemination_probe(rst, sizes=[8, 16, 32, 64], fanout=2)
    print("\ndissemination (one belief -> all N replicas, single domain, fanout 2):")
    for n, rounds in probe:
        print(f"  N={n:>3}  rounds={rounds}")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

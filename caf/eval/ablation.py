"""Stage 4: the baseline/ablation matrix (paper Table 6).

Runs one fixed incident workload through six configurations that turn the CAF
mechanisms on and off, so any difference is attributable to a specific
component. It reuses the real Stage 2/3 machinery -- grounded/ungrounded
diagnosis, the verified-admission boundary, and the gossip fabric -- rather than
re-deriving behavior.

Honest scope. The metrics reported here are the ones a deterministic simulation
can produce without fabrication:

* localization  -- did the reasoner name the true root cause (RQ1);
* systemic acc. -- isolated-vs-systemic classification correctness (RQ2);
* no-human      -- fraction of incidents resolved without human escalation;
* global calls  -- number of global-tier (frontier) invocations, the honest
                   stand-in for token cost (RQ3). We deliberately do NOT print
                   token totals, milliseconds, or CPU/memory: those require a
                   real deployment and a real model, and quoting them from a
                   simulation would be misleading;
* rejects       -- unsafe actions stopped by the admission boundary (RQ5).

The reasoner remains deterministic (Stage 2 caveat): the localization column is
a mechanism result, not an LLM benchmark. Memory's effect on ``global calls`` is
a model of shifting probability mass from the global tier to the local tier as
incidents recur, not a measured cache hit-rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from caf.agent.diagnosis import diagnose_grounded, diagnose_ungrounded
from caf.agent.repair import apply_verified
from caf.agent.signals import FaultSignal, Symptom
from caf.fabric.gossip import Fabric
from caf.fabric.node import NodeAgent
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

TESTBED = Path(__file__).resolve().parents[2] / "testbed" / "subscription"
REPLICAS = 3


@dataclass(frozen=True)
class Config:
    name: str
    rst: bool
    local: bool
    gossip: bool
    global_: bool
    memory: bool


CONFIGS = [
    Config("Traditional monitoring", rst=False, local=False, gossip=False, global_=False, memory=False),
    Config("Centralized AI-Ops", rst=False, local=False, gossip=False, global_=True, memory=False),
    Config("Centralized AI + RST", rst=True, local=False, gossip=False, global_=True, memory=False),
    Config("Local-only CAF", rst=True, local=True, gossip=False, global_=False, memory=False),
    Config("CAF without memory", rst=True, local=True, gossip=True, global_=True, memory=False),
    Config("Full CAF", rst=True, local=True, gossip=True, global_=True, memory=True),
]


@dataclass
class Incident:
    service: str
    symptom: Symptom
    true_root_cause: str
    label: str  # "isolated" | "systemic"
    signature: str
    replicas_affected: int = 1


def _workload() -> list[Incident]:
    """A fixed workload with recurrences, so memory has something to reuse."""

    return [
        Incident("payment", Symptom.pool_timeout, "payment", "isolated", "pay-pool"),
        Incident("subscription", Symptom.latency_spike, "subscription", "isolated", "sub-lat"),
        Incident("payment", Symptom.dependency_timeout, "postgres-primary", "systemic", "pg-sat", 3),
        Incident("user-api", Symptom.dependency_5xx, "auth", "systemic", "auth-out", 3),
        Incident("user-api", Symptom.dependency_5xx, "auth", "systemic", "auth-out", 3),  # recurs
        Incident("user-api", Symptom.dependency_5xx, "auth", "systemic", "auth-out", 3),  # recurs
        Incident("payment", Symptom.dependency_timeout, "postgres-primary", "systemic", "pg-sat", 3),  # recurs
        Incident("payment", Symptom.pool_timeout, "payment", "isolated", "pay-pool"),  # recurs
        # A deployment regression whose sensible action (canary rollback) fails
        # shadow verification: exercises the admission boundary (RQ5).
        Incident("payment", Symptom.deployment_regression, "payment", "isolated", "pay-regress"),
    ]


@dataclass
class Outcome:
    localized: Optional[bool] = None
    systemic_correct: Optional[bool] = None
    disposition: str = "human"  # "local" | "memory" | "global" | "human"
    global_invoked: bool = False
    unsafe_rejected: int = 0


def _pick_action(diag, node) -> Optional[str]:
    """The action to apply on an owned subject: the diagnosis's if admissible,
    otherwise the node's first permitted action."""

    permitted = set(node.permitted_actions)
    if diag.proposed_action in permitted:
        return diag.proposed_action
    return node.permitted_actions[0] if node.permitted_actions else None


def _verified(out: "Outcome", action: Optional[str], subject: str, rst: RST):
    """Apply an action through the admission boundary, counting a rejection when
    an admissible action fails verification (RQ5)."""

    res = apply_verified(action, subject, rst)
    if res.admitted and not res.verified:
        out.unsafe_rejected += 1
    return res


def _classify_systemic(incident: Incident, rst: RST) -> str:
    """Run the gossip fabric for this incident and return the observers' verdict."""

    node = rst.nodes[incident.service]
    cluster = [
        NodeAgent(f"{incident.service}-{i}", incident.service, node.failure_domain, rst)
        for i in range(REPLICAS)
    ]
    fabric = Fabric(cluster, fanout=2, gossip=True)
    subject = None
    for i in range(incident.replicas_affected):
        belief = cluster[i].observe(
            FaultSignal(service=incident.service, symptom=incident.symptom), epoch=0
        )
        subject = belief.subject
    fabric.run()
    return cluster[0].classify(subject) if subject else "isolated"


def _diagnose(config: Config, incident: Incident, rst: RST):
    if not (config.rst or config.local or config.global_):
        return None  # traditional monitoring: no reasoner at all
    signal = FaultSignal(service=incident.service, symptom=incident.symptom)
    return diagnose_grounded(signal, rst) if config.rst else diagnose_ungrounded(signal, rst)


def _process(config: Config, incident: Incident, rst: RST, memory: dict) -> Outcome:
    out = Outcome()
    diag = _diagnose(config, incident, rst)
    if diag is None:
        return out  # page a human; nothing localized or classified

    out.localized = diag.suspected_subject == incident.true_root_cause
    predicted = _classify_systemic(incident, rst) if config.gossip else "isolated"
    out.systemic_correct = predicted == incident.label

    subject = diag.suspected_subject
    node = rst.nodes.get(subject)
    owned = bool(node and node.permitted_actions)

    # Experience Memory: a recalled repair is a candidate that must still pass
    # the admission boundary (so an external subject's empty set blocks it).
    if config.memory and config.local and incident.signature in memory:
        subj, action = memory[incident.signature]
        if apply_verified(action, subj, rst).promoted:
            out.disposition = "memory"
            return out

    # Centralized configs have no local tier: everything reaches the global tier.
    if not config.local:
        if config.global_:
            out.global_invoked = True
            if owned:
                res = _verified(out, _pick_action(diag, node), subject, rst)
                out.disposition = "global" if res.promoted else "human"
            else:
                out.disposition = "human"  # external subject: escalation brief
        return out

    # Local tier present. A gossip-detected systemic fault is escalated.
    if config.gossip and predicted == "systemic":
        if config.global_:
            out.global_invoked = True
            if owned:
                res = _verified(out, _pick_action(diag, node), subject, rst)
                out.disposition = "global" if res.promoted else "human"
                if res.promoted and config.memory:
                    memory[incident.signature] = (subject, res.action)
            else:
                out.disposition = "human"
        return out

    # Treated as isolated (truly isolated, or no gossip so assumed local).
    if diag.proposed_action is None or not owned:
        out.global_invoked = config.global_
        out.disposition = "human"
        return out

    res = _verified(out, diag.proposed_action, subject, rst)
    if res.promoted:
        out.disposition = "local"
        if config.memory:
            memory[incident.signature] = (subject, res.action)
    else:
        if config.global_:
            out.global_invoked = True
            out.disposition = "global"
        else:
            out.disposition = "human"
    return out


@dataclass
class Tally:
    loc_correct: int = 0
    loc_total: int = 0
    sys_correct: int = 0
    sys_total: int = 0
    no_human: int = 0
    global_calls: int = 0
    rejects: int = 0
    n: int = 0


def _run_config(config: Config, workload: list[Incident], rst: RST) -> Tally:
    memory: dict = {}
    t = Tally()
    for incident in workload:
        out = _process(config, incident, rst, memory)
        t.n += 1
        if out.localized is not None:
            t.loc_total += 1
            t.loc_correct += int(out.localized)
        if out.systemic_correct is not None:
            t.sys_total += 1
            t.sys_correct += int(out.systemic_correct)
        if out.disposition != "human":
            t.no_human += 1
        t.global_calls += int(out.global_invoked)
        t.rejects += out.unsafe_rejected
    return t


def _frac(num: int, den: int) -> str:
    return f"{num}/{den}" if den else "-"


def run() -> int:
    rst = compile_rst(TESTBED, version="0.1.0", generated_by="caf-ablation")
    workload = _workload()

    print("Ablation matrix (Table 6) over the subscription testbed")
    print(f"workload: {len(workload)} incidents (with recurrences)\n")
    header = (
        f"{'configuration':22} {'RST':3} {'Loc':3} {'Gos':3} {'Glo':3} {'Mem':3}  "
        f"{'localize':>9} {'systemic':>9} {'no-human':>9} {'globalcalls':>12} {'rejects':>8}"
    )
    print(header)
    print("-" * len(header))

    for config in CONFIGS:
        t = _run_config(config, workload, rst)

        def flag(b: bool) -> str:
            return " x " if b else "  ."

        print(
            f"{config.name:22} {flag(config.rst)} {flag(config.local)} {flag(config.gossip)} "
            f"{flag(config.global_)} {flag(config.memory)}  "
            f"{_frac(t.loc_correct, t.loc_total):>9} "
            f"{_frac(t.sys_correct, t.sys_total):>9} "
            f"{_frac(t.no_human, t.n):>9} "
            f"{t.global_calls:>12} "
            f"{t.rejects:>8}"
        )

    print(
        "\nglobal calls = frontier-tier invocations (honest cost proxy; not token totals)."
        "\nMTTD/MTTR, tokens, CPU/mem are intentionally omitted: they need a real"
        "\ndeployment and a real model, not a deterministic simulation."
    )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

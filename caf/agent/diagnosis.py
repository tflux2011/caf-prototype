"""Root-cause diagnosis, in two arms that differ only in what they may consult.

The RQ1 claim is that RST grounding improves root-cause localization. To isolate
the self-model's contribution from the reasoner's raw capability, both arms run
the *same* symptom-to-cause policy. The grounded arm may follow the dependency
graph and read each node's closed action set; the ungrounded arm sees only the
fault signal and a symptom-keyed playbook. Any difference in outcome is therefore
attributable to structural knowledge, not to a smarter reasoner.

This is a mechanism demonstration, not an LLM accuracy benchmark. A frontier
model would sit behind exactly these two signatures; the accuracy number that
RQ1 ultimately reports requires that swap. What is shown here is the structural
consequence of grounding: correct localization of cross-service and external
faults, and correct escalation when the true subject has an empty action set.
"""

from __future__ import annotations

from typing import Optional

from caf.schema import RST, Node

from .signals import Diagnosis, FaultSignal, Symptom

# A symptom-keyed first guess. This is all an ungrounded reasoner has: it maps
# what it sees to a locally plausible action on the *observing* service, because
# it has no topology to point anywhere else.
_UNGROUNDED_PLAYBOOK: dict[Symptom, tuple[str, str]] = {
    Symptom.pool_timeout: ("restart_connection_pool", "local pool looks exhausted"),
    Symptom.dependency_timeout: ("restart_connection_pool", "a call timed out; recycle local state"),
    Symptom.dependency_5xx: ("open_circuit_breaker", "downstream returned errors; trip breaker"),
    Symptom.latency_spike: ("apply_traffic_throttle", "service is slow; shed load"),
    Symptom.retry_storm: ("open_circuit_breaker", "retries are storming; trip breaker"),
    Symptom.deployment_regression: ("trigger_canary_rollback", "recent deploy regressed; roll back canary"),
}

# Symptoms whose evidence implicates a *callee* rather than the observing
# service. For these, the grounded arm walks the dependency edges.
_DOWNSTREAM_SYMPTOMS = {Symptom.dependency_timeout, Symptom.dependency_5xx}

# Which edge kind a downstream symptom most likely came from. A database/pool
# timeout points at an SQL edge; an upstream 5xx points at an HTTP edge. This is
# topology the ungrounded arm does not have.
_SYMPTOM_EDGE_KIND: dict[Symptom, str] = {
    Symptom.dependency_timeout: "sql",
    Symptom.dependency_5xx: "http",
}


def _downstream_targets(rst: RST, service: str, symptom: Symptom) -> list[str]:
    """Callees of ``service``, preferred-edge-kind first, then stable edge order."""

    preferred = _SYMPTOM_EDGE_KIND.get(symptom)
    outbound = [(e.to, str(e.kind)) for e in rst.edges if e.from_ == service]
    preferred_hits = [to for to, kind in outbound if kind == preferred]
    others = [to for to, kind in outbound if kind != preferred]
    # De-duplicate while preserving the preferred-first ordering.
    ordered: list[str] = []
    for to in preferred_hits + others:
        if to not in ordered:
            ordered.append(to)
    return ordered


def _first_admissible(actions: list[str], node: Node) -> Optional[str]:
    """The first playbook action permitted on ``node``; None if none apply."""

    permitted = set(node.permitted_actions)
    for action in actions:
        if action in permitted:
            return action
    return None


def diagnose_grounded(signal: FaultSignal, rst: RST) -> Diagnosis:
    """Diagnose with the RST in context (closed-world lookup + constraint check).

    The reasoner (a) locates the observing service, (b) for downstream symptoms
    follows dependency edges to find the real subject, (c) refuses to propose a
    repair on a node whose action set is empty and escalates instead, and
    (d) only ever proposes an action drawn from the subject's closed set.
    """

    service = signal.service
    node = rst.nodes.get(service)
    if node is None:
        # The observing service is not in our self-model: we cannot ground the
        # diagnosis, so we escalate rather than guess.
        return Diagnosis(
            suspected_subject=service,
            hypothesis="unknown service; not present in RST",
            confidence=0.2,
            escalate=True,
            grounded=True,
        )

    # Cross-service localization: a timeout or 5xx observed here usually means a
    # callee is at fault. Follow the edges the ungrounded arm cannot see.
    if signal.symptom in _DOWNSTREAM_SYMPTOMS:
        for target in _downstream_targets(rst, service, signal.symptom):
            dep = rst.nodes.get(target)
            if dep is None:
                continue
            if dep.kind == "external_dependency" or not dep.permitted_actions:
                # The true subject is something we do not own (empty action
                # set). By construction we cannot repair it; escalate.
                return Diagnosis(
                    suspected_subject=target,
                    hypothesis=f"downstream {target} is unhealthy; no permitted local repair",
                    confidence=0.88,
                    proposed_action=None,
                    escalate=True,
                    grounded=True,
                )
            # A downstream service we *do* own: target it directly.
            action = _first_admissible(
                [_UNGROUNDED_PLAYBOOK[signal.symptom][0], "open_circuit_breaker"], dep
            )
            return Diagnosis(
                suspected_subject=target,
                hypothesis=f"downstream {target} degraded; repair at the callee",
                confidence=0.8 if action else 0.6,
                proposed_action=action,
                escalate=action is None,
                grounded=True,
            )

    # Local symptom: the observing service is the subject. Choose an action from
    # its own closed set; if nothing in the playbook is admissible, escalate.
    candidate, rationale = _UNGROUNDED_PLAYBOOK[signal.symptom]
    action = _first_admissible([candidate, "apply_traffic_throttle", "open_circuit_breaker"], node)
    return Diagnosis(
        suspected_subject=service,
        hypothesis=f"local fault at {service}: {rationale}",
        confidence=0.82 if action else 0.5,
        proposed_action=action,
        escalate=action is None,
        grounded=True,
    )


def diagnose_ungrounded(signal: FaultSignal, rst: RST) -> Diagnosis:
    """Diagnose without the topology: symptom-keyed action on the observing service.

    ``rst`` is accepted for signature parity but only its permitted-action sets
    would ever be consulted by a real system; here the ungrounded reasoner does
    not know which node is the true subject, so it acts on the service that
    reported the symptom. This is a faithful model of "what you can do with only
    local telemetry", and it is where cross-service and external faults are
    mislocalized.
    """

    action, rationale = _UNGROUNDED_PLAYBOOK[signal.symptom]
    return Diagnosis(
        suspected_subject=signal.service,
        hypothesis=f"{rationale} (no topology; assuming the fault is local)",
        confidence=0.6,
        proposed_action=action,
        escalate=False,
        grounded=False,
    )

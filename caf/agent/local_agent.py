"""Node-local triage: Algorithm 2 in single-node form.

This wires diagnosis to the verified repair pipeline. It is the L2 slice of the
paper's L1->L2->L3 loop: gossip-based systemic detection (the QueryFabric branch)
is Stage 3, so here a diagnosis that escalates, proposes no action, or fails
verification is routed to the (stubbed) global tier.
"""

from __future__ import annotations

from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict

from caf.schema import RST

from .diagnosis import diagnose_grounded, diagnose_ungrounded
from .repair import Verifier, apply_verified, default_verifier
from .signals import Diagnosis, FaultSignal, RepairOutcome

# A diagnoser turns a fault signal + self-model into a diagnosis. The two
# built-in deterministic ones live in :mod:`caf.agent.diagnosis`; a real hosted
# model (:class:`caf.reason.OpenAIReasoner`) can be injected in its place.
Diagnoser = Callable[[FaultSignal, RST], Diagnosis]


class TriageResult(BaseModel):
    """What the node did about one fault signal."""

    model_config = ConfigDict(extra="forbid")

    disposition: Literal["resolved_l2", "escalated_l3"]
    diagnosis: Diagnosis
    repair: RepairOutcome | None = None
    escalation_reason: str | None = None


def triage(
    signal: FaultSignal,
    rst: RST,
    *,
    grounded: bool = True,
    verifier: Verifier = default_verifier,
    diagnoser: Optional[Diagnoser] = None,
) -> TriageResult:
    """Run one fault signal through node-local reasoning and bounded repair.

    With ``grounded=True`` the agent reasons over the RST; with ``grounded=False``
    it uses only the symptom-keyed playbook. A ``diagnoser`` may be injected to
    replace the built-in deterministic reasoner with a real model; when given, it
    is used verbatim and ``grounded`` no longer selects the reasoner. In every
    arm, any action that is actually applied still passes through the same
    verified-admission boundary, so the safety guarantee does not depend on which
    reasoner produced the candidate.
    """

    if diagnoser is not None:
        diagnosis = diagnoser(signal, rst)
    else:
        diagnose = diagnose_grounded if grounded else diagnose_ungrounded
        diagnosis = diagnose(signal, rst)

    if diagnosis.escalate or diagnosis.proposed_action is None:
        return TriageResult(
            disposition="escalated_l3",
            diagnosis=diagnosis,
            escalation_reason=(
                "no permitted local repair for the true subject"
                if diagnosis.proposed_action is None
                else "reasoner requested escalation"
            ),
        )

    outcome = apply_verified(
        diagnosis.proposed_action, diagnosis.suspected_subject, rst, verifier
    )
    if outcome.promoted:
        return TriageResult(disposition="resolved_l2", diagnosis=diagnosis, repair=outcome)

    return TriageResult(
        disposition="escalated_l3",
        diagnosis=diagnosis,
        repair=outcome,
        escalation_reason=outcome.reason,
    )

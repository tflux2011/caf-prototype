"""Data models exchanged inside the Tier-2 agent.

These are boundary types: a fault signal enters the agent, a diagnosis and a
repair outcome leave it. They are deliberately transport-agnostic so the same
shapes can later cross the gossip fabric (Stage 3).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Symptom(str, Enum):
    """The observable class of a fault, as seen from one service.

    A symptom is what local telemetry reports. It is deliberately *not* a root
    cause: ``dependency_timeout`` says "a call I made timed out", not "which
    node is actually broken". Turning a symptom into a subject is the job of
    diagnosis, and that is precisely where the RST changes the answer.
    """

    pool_timeout = "pool_timeout"
    dependency_timeout = "dependency_timeout"
    dependency_5xx = "dependency_5xx"
    latency_spike = "latency_spike"
    retry_storm = "retry_storm"
    deployment_regression = "deployment_regression"


class FaultSignal(BaseModel):
    """A fault as observed at a single service.

    ``true_root_cause`` is ground truth used only by the evaluation harness to
    score localization; the diagnosers never read it.
    """

    model_config = ConfigDict(extra="forbid")

    service: str
    symptom: Symptom
    # Free-form counters, e.g. {"pool_timeouts": 7}. Evidence only; not trusted
    # as structure.
    evidence: dict[str, int] = Field(default_factory=dict)
    # Ground truth for scoring. Never consulted by a diagnoser.
    true_root_cause: Optional[str] = None


class Diagnosis(BaseModel):
    """The output of the reasoner: where it thinks the fault is and what to do."""

    model_config = ConfigDict(extra="forbid")

    suspected_subject: str
    hypothesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    proposed_action: Optional[str] = None
    escalate: bool = False
    # Whether the reasoner had the RST in context. Recorded for auditing the
    # comparison, not used in any decision.
    grounded: bool = False


class RepairOutcome(BaseModel):
    """The result of pushing one candidate action through ApplyVerified."""

    model_config = ConfigDict(extra="forbid")

    action: str
    subject: str
    admitted: bool  # action is a member of the subject's closed action set
    verified: bool  # passed shadow verification
    promoted: bool  # reached production: admitted AND verified
    strategy: str  # the verification strategy that was applied
    reason: str

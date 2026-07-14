"""The diagnostic-hypothesis (belief) exchanged over the gossip fabric.

Mirrors the belief tuple of the paper's gossip section. Provenance fields
(origin, artifact_version, evidence_digest, interval, epoch) are what let a
receiver discount stale, version-mismatched, or correlated evidence.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# A belief is keyed by (origin, subject, epoch): the same node's claim about the
# same subject within the same incident. Anti-entropy gossip re-delivers beliefs,
# so this key is what makes fusion idempotent under duplicate delivery.
BeliefKey = tuple[str, str, int]


class Belief(BaseModel):
    """A confidence-weighted, evidence-bearing claim about a root cause."""

    model_config = ConfigDict(extra="forbid")

    origin: str  # the node instance that formed the belief
    artifact_version: str  # RST version the belief was reasoned against
    subject: str  # the suspected root-cause node id
    hypothesis: str  # human-readable root-cause claim (agreement is by equality)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_digest: str = ""
    epoch: int = 0  # incident epoch; groups beliefs about one incident
    origin_domain: Optional[str] = None  # failure domain of the origin node
    model_id: str = "det-reasoner-0.1"
    repair_outcome: Optional[str] = None

    def key(self) -> BeliefKey:
        return (self.origin, self.subject, self.epoch)

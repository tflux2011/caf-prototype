"""A fabric node: forms beliefs, ingests peers' beliefs, and classifies faults.

Each node maintains, per suspected subject, a fused confidence and the set of
distinct origins that have reported it. A subject is classified *systemic* only
when both hold: fused confidence crosses the consensus threshold AND a quorum of
distinct origins corroborate it. Confidence alone is not enough -- that is the
whole point of RQ2. One very sure node is still just one node.

Corroboration is agreement on the fault *locus* (the subject), not on the
mechanism string. Two nodes that both localize a fault to the same subject
corroborate that it is faulty even when they disagree on *why* -- e.g. one
replica reports "connection pool exhausted" (the root cause) while an upstream
caller reports "downstream 5xx" (the very same fault observed from the other
side). Penalizing that as a contradiction, as an earlier version did, made a
genuinely systemic fault look isolated. Mechanism disagreement is instead
recorded separately as a divergence signal and never lowers locus confidence.
"""

from __future__ import annotations

from typing import Literal, Optional

from caf.agent.diagnosis import diagnose_grounded
from caf.agent.signals import FaultSignal
from caf.schema import RST

from .belief import Belief, BeliefKey
from .fusion import fuse

Classification = Literal["isolated", "systemic"]


class NodeAgent:
    """One agent instance in the fabric (a service replica)."""

    def __init__(
        self,
        instance_id: str,
        service: str,
        failure_domain: Optional[str],
        rst: RST,
        *,
        eta: float = 0.5,
        theta_cons: float = 0.7,
        quorum: int = 2,
    ) -> None:
        self.id = instance_id
        self.service = service
        self.domain = failure_domain
        self.rst = rst
        self.eta = eta
        self.theta_cons = theta_cons
        self.quorum = quorum

        self.seen: set[BeliefKey] = set()
        self.store: dict[BeliefKey, Belief] = {}
        self.fused: dict[str, float] = {}
        self.reporters: dict[str, set[str]] = {}
        self.hypotheses: dict[str, set[str]] = {}  # distinct mechanisms per subject

    # -- belief formation ----------------------------------------------------

    def form_belief(self, signal: FaultSignal, *, epoch: int) -> Belief:
        """Diagnose a local fault (grounded) and package it as a belief."""

        diag = diagnose_grounded(signal, self.rst)
        return Belief(
            origin=self.id,
            artifact_version=self.rst.version,
            subject=diag.suspected_subject,
            hypothesis=diag.hypothesis,
            confidence=diag.confidence,
            evidence_digest=";".join(f"{k}={v}" for k, v in sorted(signal.evidence.items())),
            epoch=epoch,
            origin_domain=self.domain,
        )

    def observe(self, signal: FaultSignal, *, epoch: int) -> Belief:
        """Form a belief from a local observation and ingest it as our own."""

        belief = self.form_belief(signal, epoch=epoch)
        self.ingest(belief)
        return belief

    # -- belief ingestion ----------------------------------------------------

    def ingest(self, belief: Belief) -> bool:
        """Fuse an unseen belief. Returns True iff it was new (idempotency)."""

        key = belief.key()
        if key in self.seen:
            return False
        self.seen.add(key)
        self.store[key] = belief

        subject = belief.subject
        if subject not in self.fused:
            # Initialize the estimate to the first belief's own confidence
            # rather than fusing up from zero, which would understate a lone
            # strong observation.
            self.fused[subject] = belief.confidence
            self.hypotheses[subject] = {belief.hypothesis}
            self.reporters[subject] = {belief.origin}
            return True

        # Agreement on the subject is corroboration of the locus, regardless of
        # whether the mechanism string matches. A genuine contradiction would
        # require an explicit *healthy* assertion about the subject, which this
        # belief model does not emit, so we never take the contradiction path
        # here. Mechanism disagreement is tracked below as divergence only.
        self.hypotheses[subject].add(belief.hypothesis)
        self.fused[subject] = fuse(
            self.fused[subject],
            belief.confidence,
            same_hypothesis=True,
            contradiction=False,
            eta=self.eta,
        )
        self.reporters[subject].add(belief.origin)
        return True

    def digest(self) -> list[Belief]:
        """All beliefs this node holds (anti-entropy exchange payload)."""

        return list(self.store.values())

    # -- classification ------------------------------------------------------

    def classify(self, subject: str) -> Classification:
        """Isolated vs systemic for a subject, per the consensus predicate."""

        confidence = self.fused.get(subject, 0.0)
        corroborators = len(self.reporters.get(subject, ()))
        if confidence >= self.theta_cons and corroborators >= self.quorum:
            return "systemic"
        return "isolated"

    def mechanisms(self, subject: str) -> set[str]:
        """Distinct hypotheses reported for a subject (the mechanism set)."""

        return set(self.hypotheses.get(subject, ()))

    def mechanism_divergent(self, subject: str) -> bool:
        """True iff reporters agree on *where* the fault is but not on *why*."""

        return len(self.hypotheses.get(subject, ())) > 1

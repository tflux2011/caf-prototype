"""The verified repair pipeline: ApplyVerified and the admission boundary.

This module realizes Theorem 1 (verified production-admission) and Property 1
(bounded action) structurally, not by trust in the reasoner:

    an action reaches "production" iff it is a member of the subject's closed
    permitted-action set AND its shadow verification predicate returns true.

The verifier is a pluggable callable. The default verifier models the
paper's action-specific verification *strategies*: each action declares how it
is checked (static policy, sandbox simulation, traffic replay, canary, ...), and
some actions are modeled as failing verification so the reject/escalate path is
exercised.
"""

from __future__ import annotations

from typing import Callable, Optional

from caf.schema import RST

from .signals import RepairOutcome

# A Verifier decides whether a candidate action, already known to be admissible,
# passes its shadow check. It returns (passed, strategy_name).
Verifier = Callable[[str, str, RST], "VerifyResult"]


class VerifyResult:
    """The outcome of a shadow verification run."""

    __slots__ = ("passed", "strategy", "detail")

    def __init__(self, passed: bool, strategy: str, detail: str = "") -> None:
        self.passed = passed
        self.strategy = strategy
        self.detail = detail


# Per-action verification strategy, mirroring the paper's claim that the
# strategy is declared alongside each action rather than being one-size-fits-all.
_ACTION_STRATEGY: dict[str, str] = {
    "apply_traffic_throttle": "recorded-traffic-replay",
    "restart_connection_pool": "sandbox-simulation",
    "trigger_canary_rollback": "canary-execution",
    "open_circuit_breaker": "static-policy-validation",
    "scale_consumers": "canary-execution",
    "shed_load": "static-policy-validation",
}

# Actions the default verifier models as failing in the shadow, so the
# reject/escalate branch of Theorem 1 is demonstrably reachable. A canary
# rollback is the canonical "looks fine statically, fails under load" case.
_SHADOW_FAILS: frozenset[str] = frozenset({"trigger_canary_rollback"})


def default_verifier(action: str, subject: str, rst: RST) -> VerifyResult:
    """A deterministic stand-in for shadow-container verification.

    Real verification runs the action against production-like state in an
    isolated shadow. Here we return a fixed verdict per action so the pipeline's
    control flow — promote on pass, reject/escalate on fail — is testable.
    """

    strategy = _ACTION_STRATEGY.get(action, "static-policy-validation")
    if action in _SHADOW_FAILS:
        return VerifyResult(False, strategy, "candidate regressed under shadow load")
    return VerifyResult(True, strategy, "shadow checks passed")


def apply_verified(
    action: Optional[str],
    subject: str,
    rst: RST,
    verifier: Verifier = default_verifier,
) -> RepairOutcome:
    """Push one candidate through the admission boundary of Theorem 1.

    Promotion to production requires BOTH membership in the closed action set
    (Property 1) AND a passing verification predicate. Neither is sufficient
    alone, and the shadow never shares a write path with production, so a
    candidate under test cannot mutate production before promotion.
    """

    node = rst.nodes.get(subject)
    if node is None:
        return RepairOutcome(
            action=action or "",
            subject=subject,
            admitted=False,
            verified=False,
            promoted=False,
            strategy="none",
            reason="subject not present in RST",
        )

    if action is None:
        return RepairOutcome(
            action="",
            subject=subject,
            admitted=False,
            verified=False,
            promoted=False,
            strategy="none",
            reason="no candidate action proposed",
        )

    admitted = action in set(node.permitted_actions)
    if not admitted:
        # Property 1: an action outside the closed set is unrepresentable in
        # production. It is rejected before any verification is attempted.
        return RepairOutcome(
            action=action,
            subject=subject,
            admitted=False,
            verified=False,
            promoted=False,
            strategy="none",
            reason="action not in the subject's permitted-action set",
        )

    result = verifier(action, subject, rst)
    promoted = admitted and result.passed
    return RepairOutcome(
        action=action,
        subject=subject,
        admitted=True,
        verified=result.passed,
        promoted=promoted,
        strategy=result.strategy,
        reason=result.detail if result.passed else f"verification failed: {result.detail}",
    )

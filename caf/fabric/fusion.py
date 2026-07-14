"""Belief fusion: the bounded, agreement-weighted rule of Eq. (4).

    k'(s) = k(s) + eta*(k_b - k(s))*1[same hypothesis]
                 - eta*k(s)*1[contradiction]           , clamped to [0, 1].

This is deliberately the paper's *preliminary* heuristic. It does not model
uncalibrated confidence, correlated observations, message-order dependence, or
adversarial injection; a principled successor would use subjective logic,
Dempster-Shafer, or Bayesian accumulation. We keep the simple form so the
mechanism -- corroboration raises confidence, contradiction lowers it -- is
transparent and testable.
"""

from __future__ import annotations


def fuse(
    current: float,
    incoming_confidence: float,
    *,
    same_hypothesis: bool,
    contradiction: bool,
    eta: float,
) -> float:
    """Apply one fusion step and clamp to [0, 1]."""

    if not 0.0 < eta < 1.0:
        raise ValueError("learning rate eta must be in (0, 1)")

    kappa = current
    if same_hypothesis:
        kappa = kappa + eta * (incoming_confidence - kappa)
    if contradiction:
        kappa = kappa - eta * kappa
    return max(0.0, min(1.0, kappa))

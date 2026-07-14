"""Tests for the belief fusion rule (Eq. 4)."""

import pytest

from caf.fabric.fusion import fuse


def test_agreement_moves_toward_incoming():
    # Same hypothesis: estimate moves a fraction eta toward the incoming value.
    out = fuse(0.5, 0.9, same_hypothesis=True, contradiction=False, eta=0.5)
    assert out == pytest.approx(0.7)


def test_contradiction_decays_estimate():
    out = fuse(0.8, 0.9, same_hypothesis=False, contradiction=True, eta=0.5)
    assert out == pytest.approx(0.4)


def test_result_is_clamped_to_unit_interval():
    assert fuse(1.0, 1.0, same_hypothesis=True, contradiction=False, eta=0.9) <= 1.0
    assert fuse(0.0, 0.0, same_hypothesis=False, contradiction=True, eta=0.9) >= 0.0


def test_repeated_agreement_converges_toward_peer_confidence():
    kappa = 0.2
    for _ in range(50):
        kappa = fuse(kappa, 0.9, same_hypothesis=True, contradiction=False, eta=0.5)
    assert kappa == pytest.approx(0.9, abs=1e-6)


def test_invalid_eta_rejected():
    with pytest.raises(ValueError):
        fuse(0.5, 0.5, same_hypothesis=True, contradiction=False, eta=0.0)
    with pytest.raises(ValueError):
        fuse(0.5, 0.5, same_hypothesis=True, contradiction=False, eta=1.0)

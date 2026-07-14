"""Tests for the verified repair pipeline (Theorem 1 / Property 1)."""

from pathlib import Path

from caf.agent.repair import apply_verified, default_verifier
from caf.agent.signals import RepairOutcome
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

TESTBED = Path(__file__).resolve().parents[1] / "testbed" / "subscription"


def _rst() -> RST:
    return compile_rst(TESTBED, version="0.1.0", generated_by="pytest")


def test_admissible_and_verified_action_is_promoted():
    out = apply_verified("restart_connection_pool", "payment", _rst())
    assert out.admitted is True
    assert out.verified is True
    assert out.promoted is True


def test_action_outside_permitted_set_is_rejected_before_verification():
    # scale_consumers is a known action but NOT in payment's permitted set.
    out = apply_verified("scale_consumers", "payment", _rst())
    assert out.admitted is False
    assert out.promoted is False
    assert out.strategy == "none"  # verification never attempted


def test_action_on_external_dependency_can_never_be_admitted():
    # postgres-primary has an empty action set: nothing is admissible.
    out = apply_verified("restart_connection_pool", "postgres-primary", _rst())
    assert out.admitted is False
    assert out.promoted is False


def test_verification_failure_blocks_promotion():
    # trigger_canary_rollback is admissible on payment but the default verifier
    # models it as failing in the shadow.
    out = apply_verified("trigger_canary_rollback", "payment", _rst())
    assert out.admitted is True
    assert out.verified is False
    assert out.promoted is False
    assert "verification failed" in out.reason


def test_no_candidate_action_is_not_promoted():
    out = apply_verified(None, "payment", _rst())
    assert isinstance(out, RepairOutcome)
    assert out.promoted is False


def test_theorem1_invariant_over_all_actions():
    # Exhaustive check: for every (node, action) pair, promotion implies both
    # admission and verification. This is the machine-checked form of Theorem 1.
    rst = _rst()
    all_actions = [
        "apply_traffic_throttle",
        "restart_connection_pool",
        "trigger_canary_rollback",
        "open_circuit_breaker",
        "scale_consumers",
        "shed_load",
    ]
    for node_id in rst.nodes:
        for action in all_actions:
            out = apply_verified(action, node_id, rst, default_verifier)
            if out.promoted:
                assert out.admitted and out.verified
                assert action in set(rst.nodes[node_id].permitted_actions)

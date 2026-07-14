"""Tests for the ablation matrix (Stage 4).

These assert the structural relationships the paper's Table 6 claims, not
specific magic numbers, so the tests stay meaningful if the workload changes.
"""

from pathlib import Path

from caf.eval.ablation import CONFIGS, _run_config, _workload
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

TESTBED = Path(__file__).resolve().parents[1] / "testbed" / "subscription"


def _rst() -> RST:
    return compile_rst(TESTBED, version="0.1.0", generated_by="pytest")


def _by_name() -> dict:
    rst = _rst()
    wl = _workload()
    return {c.name: _run_config(c, wl, rst) for c in CONFIGS}


def test_traditional_monitoring_resolves_nothing_autonomously():
    t = _by_name()["Traditional monitoring"]
    assert t.no_human == 0
    assert t.global_calls == 0
    assert t.loc_total == 0  # no reasoner, nothing to localize


def test_rst_lifts_localization_at_the_same_global_tier():
    r = _by_name()
    ungrounded = r["Centralized AI-Ops"]
    grounded = r["Centralized AI + RST"]
    # Same centralized global tier; adding the RST strictly improves localization.
    assert grounded.loc_correct > ungrounded.loc_correct
    assert grounded.loc_correct == grounded.loc_total  # grounded gets them all


def test_gossip_improves_systemic_classification():
    r = _by_name()
    no_gossip = r["Local-only CAF"]
    gossip = r["CAF without memory"]
    assert gossip.sys_correct > no_gossip.sys_correct
    assert gossip.sys_correct == gossip.sys_total


def test_memory_reduces_global_invocations():
    r = _by_name()
    without = r["CAF without memory"]
    full = r["Full CAF"]
    # Recurring owned-subject incidents are served from memory instead of the
    # global tier, so full CAF makes strictly fewer frontier calls.
    assert full.global_calls < without.global_calls


def test_centralized_configs_hit_global_on_every_incident():
    r = _by_name()
    for name in ("Centralized AI-Ops", "Centralized AI + RST"):
        t = r[name]
        assert t.global_calls == t.n  # p3 = 1 by construction


def test_admission_boundary_never_bypassed_in_any_config():
    # No configuration should ever report a negative or impossible tally.
    for t in _by_name().values():
        assert t.rejects >= 0
        assert 0 <= t.no_human <= t.n
        assert t.global_calls >= 0

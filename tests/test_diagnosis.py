"""Tests for the grounded/ungrounded diagnosis contrast (RQ1 mechanism)."""

from pathlib import Path

from caf.agent.diagnosis import diagnose_grounded, diagnose_ungrounded
from caf.agent.local_agent import triage
from caf.agent.signals import FaultSignal, Symptom
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

TESTBED = Path(__file__).resolve().parents[1] / "testbed" / "subscription"


def _rst() -> RST:
    return compile_rst(TESTBED, version="0.1.0", generated_by="pytest")


def test_grounded_localizes_external_root_cause_and_escalates():
    rst = _rst()
    signal = FaultSignal(
        service="payment",
        symptom=Symptom.dependency_timeout,
        true_root_cause="postgres-primary",
    )
    diag = diagnose_grounded(signal, rst)
    assert diag.suspected_subject == "postgres-primary"
    assert diag.proposed_action is None
    assert diag.escalate is True

    result = triage(signal, rst, grounded=True)
    assert result.disposition == "escalated_l3"


def test_ungrounded_mislocalizes_external_root_cause_to_observer():
    rst = _rst()
    signal = FaultSignal(
        service="payment",
        symptom=Symptom.dependency_timeout,
        true_root_cause="postgres-primary",
    )
    diag = diagnose_ungrounded(signal, rst)
    # Blames the observing service, not the real (external) subject.
    assert diag.suspected_subject == "payment"
    assert diag.suspected_subject != signal.true_root_cause


def test_grounded_follows_edge_to_downstream_service():
    rst = _rst()
    signal = FaultSignal(
        service="subscription",
        symptom=Symptom.dependency_timeout,
        true_root_cause="payment",
    )
    diag = diagnose_grounded(signal, rst)
    assert diag.suspected_subject == "payment"


def test_grounded_local_fault_stays_local_and_resolves():
    rst = _rst()
    signal = FaultSignal(
        service="payment",
        symptom=Symptom.pool_timeout,
        true_root_cause="payment",
    )
    diag = diagnose_grounded(signal, rst)
    assert diag.suspected_subject == "payment"
    assert diag.proposed_action == "restart_connection_pool"

    result = triage(signal, rst, grounded=True)
    assert result.disposition == "resolved_l2"
    assert result.repair is not None and result.repair.promoted is True


def test_grounded_never_proposes_inadmissible_action():
    rst = _rst()
    from caf.eval.scenarios import scenarios

    for signal in scenarios():
        diag = diagnose_grounded(signal, rst)
        if diag.proposed_action is not None:
            subject = rst.nodes[diag.suspected_subject]
            assert diag.proposed_action in set(subject.permitted_actions)

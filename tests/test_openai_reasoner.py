"""Tests for the OpenAI-backed reasoner (Phase D), fully offline.

A stub transport stands in for the network so these tests never call OpenAI.
They verify prompt construction (grounding), strict JSON parsing, token
accounting, and -- most importantly -- that the Theorem 1 admission boundary is
re-imposed in code regardless of what the model returns.
"""

import json
from pathlib import Path

import pytest

from caf.agent.signals import FaultSignal, Symptom
from caf.reason import MissingAPIKey, OpenAIReasoner
from caf.reason.openai_reasoner import ChatResult, ReasonerError
from caf.rst_compiler.compiler import compile_rst

TESTBED = Path(__file__).resolve().parents[1] / "testbed" / "subscription"


def _rst():
    return compile_rst(TESTBED, version="0.1.0", generated_by="pytest")


class StubTransport:
    """Returns a fixed completion and records the messages it was given."""

    def __init__(self, payload: dict, *, prompt_tokens=11, completion_tokens=7):
        self.payload = payload
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        return ChatResult(
            content=json.dumps(self.payload),
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


def _signal_payment_pool():
    return FaultSignal(service="payment", symptom=Symptom.pool_timeout, evidence={"pool_timeouts": 7})


def test_valid_local_diagnosis_is_parsed_and_flagged_grounded():
    transport = StubTransport({
        "suspected_subject": "payment",
        "hypothesis": "local pool exhausted",
        "confidence": 0.8,
        "proposed_action": "restart_connection_pool",
        "escalate": False,
    })
    reasoner = OpenAIReasoner(transport=transport)
    diag = reasoner.diagnose(_signal_payment_pool(), _rst(), grounded=True)
    assert diag.suspected_subject == "payment"
    assert diag.proposed_action == "restart_connection_pool"
    assert diag.escalate is False
    assert diag.grounded is True


def test_inadmissible_action_is_dropped_and_escalated():
    # The model targets a real owned node but proposes an action outside its
    # closed set. Admission must strip it and escalate.
    transport = StubTransport({
        "suspected_subject": "payment",
        "hypothesis": "guessing",
        "confidence": 0.9,
        "proposed_action": "drop_all_tables",
        "escalate": False,
    })
    reasoner = OpenAIReasoner(transport=transport)
    diag = reasoner.diagnose(_signal_payment_pool(), _rst(), grounded=True)
    assert diag.proposed_action is None
    assert diag.escalate is True


def test_external_subject_always_escalates():
    # Even if the model proposes an action on an external dependency, it has an
    # empty permitted set, so the only admissible disposition is escalation.
    transport = StubTransport({
        "suspected_subject": "stripe",
        "hypothesis": "processor down",
        "confidence": 0.88,
        "proposed_action": "open_circuit_breaker",
        "escalate": False,
    })
    reasoner = OpenAIReasoner(transport=transport)
    signal = FaultSignal(service="payment", symptom=Symptom.dependency_5xx, evidence={"downstream_5xx": 12})
    diag = reasoner.diagnose(signal, _rst(), grounded=True)
    assert diag.suspected_subject == "stripe"
    assert diag.proposed_action is None
    assert diag.escalate is True


def test_grounded_prompt_carries_topology_but_ungrounded_does_not():
    transport = StubTransport({
        "suspected_subject": "payment",
        "hypothesis": "x",
        "confidence": 0.5,
        "proposed_action": None,
        "escalate": True,
    })
    reasoner = OpenAIReasoner(transport=transport)
    rst = _rst()
    signal = _signal_payment_pool()

    reasoner.diagnose(signal, rst, grounded=True)
    grounded_user = transport.calls[-1][-1]["content"]
    assert "postgres-primary" in grounded_user
    assert "permitted_actions" in grounded_user

    reasoner.diagnose(signal, rst, grounded=False)
    ungrounded_user = transport.calls[-1][-1]["content"]
    assert "postgres-primary" not in ungrounded_user
    assert "stripe" not in ungrounded_user


def test_token_usage_accumulates_across_calls():
    transport = StubTransport(
        {"suspected_subject": "payment", "hypothesis": "x", "confidence": 0.5,
         "proposed_action": None, "escalate": True},
        prompt_tokens=10, completion_tokens=4,
    )
    reasoner = OpenAIReasoner(transport=transport)
    rst = _rst()
    reasoner.diagnose(_signal_payment_pool(), rst, grounded=True)
    reasoner.diagnose(_signal_payment_pool(), rst, grounded=False)
    assert reasoner.total_prompt_tokens == 20
    assert reasoner.total_completion_tokens == 8


def test_invalid_json_raises_reasoner_error():
    class BadTransport:
        def __call__(self, messages):
            return ChatResult(content="not json at all")

    reasoner = OpenAIReasoner(transport=BadTransport())
    with pytest.raises(ReasonerError):
        reasoner.diagnose(_signal_payment_pool(), _rst(), grounded=True)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingAPIKey):
        OpenAIReasoner()

"""Tier-2 (node-local) reasoning and the verified repair pipeline.

This package implements the parts of the paper that can be demonstrated
deterministically today:

* the verified-admission boundary (Theorem 1) and bounded action (Property 1),
* node-local triage (Algorithm 2, single-node form; gossip is Stage 3), and
* the grounded-vs-ungrounded diagnosis contrast (RQ1).

The reasoner is intentionally a small, deterministic policy. The paper's global
tier is a frontier model; here the diagnosis functions expose the exact
interface such a model would sit behind, so swapping in an LLM is a local change
that does not touch the repair pipeline or its safety guarantee.
"""

from __future__ import annotations

from .signals import Diagnosis, FaultSignal, RepairOutcome, Symptom
from .diagnosis import diagnose_grounded, diagnose_ungrounded
from .repair import apply_verified, default_verifier
from .local_agent import TriageResult, triage

__all__ = [
    "Diagnosis",
    "FaultSignal",
    "RepairOutcome",
    "Symptom",
    "diagnose_grounded",
    "diagnose_ungrounded",
    "apply_verified",
    "default_verifier",
    "TriageResult",
    "triage",
]

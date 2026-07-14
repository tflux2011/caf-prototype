"""RQ1: does RST grounding improve root-cause localization?

Runs every scenario through both the grounded and ungrounded reasoner and scores
three structural properties per arm:

* localization: did the reasoner name the true root-cause node?
* safe disposition: for a fault whose true subject has an empty action set, did
  the agent escalate instead of "repairing" the wrong node?
* admissible action: if it proposed a repair, was that repair permitted on the
  node it targeted?

This measures the *mechanism*, not an LLM's accuracy. The two arms share one
decision procedure; only access to the topology differs, so the gap is the
self-model's contribution — the isolation RQ1 asks for.
"""

from __future__ import annotations

from pathlib import Path

from caf.agent.diagnosis import diagnose_grounded, diagnose_ungrounded
from caf.agent.local_agent import triage
from caf.agent.signals import FaultSignal
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

from .scenarios import scenarios

TESTBED = Path(__file__).resolve().parents[2] / "testbed" / "subscription"


def _external_or_unowned(rst: RST, node_id: str) -> bool:
    node = rst.nodes.get(node_id)
    return node is None or node.kind == "external_dependency" or not node.permitted_actions


def _score_arm(signal: FaultSignal, rst: RST, grounded: bool) -> dict:
    diagnose = diagnose_grounded if grounded else diagnose_ungrounded
    diag = diagnose(signal, rst)
    result = triage(signal, rst, grounded=grounded)

    localized = diag.suspected_subject == signal.true_root_cause
    # If the true subject cannot be locally repaired, the only safe disposition
    # is escalation. Otherwise a local resolution is the correct outcome.
    must_escalate = _external_or_unowned(rst, signal.true_root_cause or "")
    if must_escalate:
        safe = result.disposition == "escalated_l3"
    else:
        safe = True  # repairing an owned node (or escalating it) is not unsafe

    # Was any proposed action admissible on the node it targeted?
    if diag.proposed_action is None:
        admissible = True  # escalation proposes nothing; vacuously safe
    else:
        subject = rst.nodes.get(diag.suspected_subject)
        admissible = bool(subject and diag.proposed_action in set(subject.permitted_actions))

    return {
        "subject": diag.suspected_subject,
        "action": diag.proposed_action or "-",
        "disposition": result.disposition,
        "localized": localized,
        "safe": safe,
        "admissible": admissible,
    }


def run() -> int:
    rst = compile_rst(TESTBED, version="0.1.0", generated_by="caf-eval")
    cases = scenarios()

    rows = []
    tallies = {
        "grounded": {"localized": 0, "safe": 0, "admissible": 0},
        "ungrounded": {"localized": 0, "safe": 0, "admissible": 0},
    }
    for signal in cases:
        g = _score_arm(signal, rst, grounded=True)
        u = _score_arm(signal, rst, grounded=False)
        rows.append((signal, g, u))
        for arm_name, arm in (("grounded", g), ("ungrounded", u)):
            for key in ("localized", "safe", "admissible"):
                if arm[key]:
                    tallies[arm_name][key] += 1

    n = len(cases)
    _print_report(rows, tallies, n)
    return 0


def _mark(ok: bool) -> str:
    return "OK " if ok else "XX "


def _print_report(rows, tallies, n: int) -> None:
    print("RQ1 - RST grounding vs ungrounded reasoning")
    print(f"testbed: subscription  |  scenarios: {n}\n")
    header = (
        f"{'fault (service/symptom)':32}  {'truth':16}  "
        f"{'grounded subject':18} {'g':3} {'safe':5}  "
        f"{'ungrounded subject':18} {'u':3} {'safe':5}"
    )
    print(header)
    print("-" * len(header))
    for signal, g, u in rows:
        fault = f"{signal.service}/{signal.symptom.value}"
        print(
            f"{fault:32}  {str(signal.true_root_cause):16}  "
            f"{g['subject']:18} {_mark(g['localized'])} {_mark(g['safe']):5}  "
            f"{u['subject']:18} {_mark(u['localized'])} {_mark(u['safe']):5}"
        )

    print("\nsummary (higher is better):")
    print(f"{'metric':22} {'grounded':>10} {'ungrounded':>12}")
    for key, label in (
        ("localized", "root-cause localized"),
        ("safe", "safe disposition"),
        ("admissible", "admissible action"),
    ):
        g = tallies["grounded"][key]
        u = tallies["ungrounded"][key]
        print(f"{label:22} {g:>7}/{n}   {u:>8}/{n}")


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

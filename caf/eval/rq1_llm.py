"""RQ1 measured with a REAL model (Phase D).

Mirror of :mod:`caf.eval.rq1`, but the diagnosis comes from a real OpenAI model
instead of the deterministic playbook. It runs every scenario through the
grounded and ungrounded arms and scores the same three structural properties,
then reports real token usage. The only difference between the arms is whether
the RST slice is placed in the prompt, so any accuracy gap is the self-model's
contribution -- now demonstrated with a genuine reasoner.

Requires ``OPENAI_API_KEY`` in the environment. If it is absent the harness
prints how to enable it and exits cleanly, so offline runs never fail.

Run:  caf-eval-rq1-llm
"""

from __future__ import annotations

from pathlib import Path

from caf.agent.signals import Diagnosis, FaultSignal
from caf.reason import MissingAPIKey, OpenAIReasoner, ReasonerError
from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST

from .scenarios import scenarios

TESTBED = Path(__file__).resolve().parents[2] / "testbed" / "subscription"


def _external_or_unowned(rst: RST, node_id: str) -> bool:
    node = rst.nodes.get(node_id)
    return node is None or node.kind == "external_dependency" or not node.permitted_actions


def _disposition(diag: Diagnosis) -> str:
    return "escalated_l3" if (diag.escalate or diag.proposed_action is None) else "resolved_l2"


def _score_arm(diag: Diagnosis, signal: FaultSignal, rst: RST) -> dict:
    localized = diag.suspected_subject == signal.true_root_cause
    must_escalate = _external_or_unowned(rst, signal.true_root_cause or "")
    disposition = _disposition(diag)
    safe = disposition == "escalated_l3" if must_escalate else True
    if diag.proposed_action is None:
        admissible = True
    else:
        subject = rst.nodes.get(diag.suspected_subject)
        admissible = bool(subject and diag.proposed_action in set(subject.permitted_actions))
    return {
        "subject": diag.suspected_subject,
        "action": diag.proposed_action or "-",
        "disposition": disposition,
        "localized": localized,
        "safe": safe,
        "admissible": admissible,
    }


def run() -> int:
    try:
        reasoner = OpenAIReasoner()
    except MissingAPIKey:
        print(
            "RQ1 (LLM) skipped: OPENAI_API_KEY is not set.\n"
            "  export OPENAI_API_KEY=sk-...   # then re-run caf-eval-rq1-llm\n"
            "  optional: export CAF_OPENAI_MODEL=gpt-4o-mini"
        )
        return 0

    rst = compile_rst(TESTBED, version="0.1.0", generated_by="caf-eval-rq1-llm")
    cases = scenarios()

    rows = []
    tallies = {
        "grounded": {"localized": 0, "safe": 0, "admissible": 0},
        "ungrounded": {"localized": 0, "safe": 0, "admissible": 0},
    }
    try:
        for signal in cases:
            g = _score_arm(reasoner.diagnose(signal, rst, grounded=True), signal, rst)
            u = _score_arm(reasoner.diagnose(signal, rst, grounded=False), signal, rst)
            rows.append((signal, g, u))
            for name, arm in (("grounded", g), ("ungrounded", u)):
                for key in ("localized", "safe", "admissible"):
                    if arm[key]:
                        tallies[name][key] += 1
    except ReasonerError as exc:
        print(f"RQ1 (LLM) aborted: {exc}")
        return 1

    _print_report(rows, tallies, len(cases), reasoner)
    return 0


def _mark(ok: bool) -> str:
    return "OK " if ok else "XX "


def _print_report(rows, tallies, n: int, reasoner: OpenAIReasoner) -> None:
    print("RQ1 (REAL MODEL) - RST grounding vs ungrounded reasoning")
    print(f"model: {reasoner.model}  |  temperature: {reasoner.temperature}")
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

    total = reasoner.total_prompt_tokens + reasoner.total_completion_tokens
    print(
        f"\ntokens (real): prompt={reasoner.total_prompt_tokens} "
        f"completion={reasoner.total_completion_tokens} total={total} "
        f"over {2 * n} calls"
    )


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

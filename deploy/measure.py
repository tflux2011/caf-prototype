#!/usr/bin/env python3
"""Phase E -- real measurement harness for the running CAF testbed.

This does not simulate or fabricate anything: it injects genuine faults into the
live Docker stack (reusing :mod:`faultmatrix`'s injection + clean-baseline reset)
and records only what the agents actually did, read over HTTP from their live
``/state`` endpoints and from ``docker stats``.

For each fault case, over ``N`` independent trials, it measures:

* **time-to-disposition** -- wall-clock seconds from fault injection to the first
  disposition event surfacing in an agent's ``/state``. Detection, reasoning and
  disposition happen in a single agent tick, so this one latency honestly bundles
  the scrape cadence (``AGENT_INTERVAL_S``) and, when the OpenAI reasoner is
  active, the real model round-trip. It is therefore an upper bound on end-to-end
  autonomic response, not an isolated reasoning time;
* **LLM tokens per fault episode** -- the prompt/completion totals the fabric's
  agents actually spent (delta from a zeroed baseline; the reason counters reset
  when the agents restart during :func:`faultmatrix.reset`). Zero when the stack
  runs the deterministic reasoner;
* **applied repair** -- the L2 action a node actually promoted, when any;
* **agent CPU / memory** -- sampled from ``docker stats`` while the fault is
  settled, to size the sidecar's real footprint.

Numbers are aggregated as mean / stdev / min / max across trials. Latency is
quantized by the 2s scrape interval by design; that quantization is a real
property of the deployment and is reported, not hidden.

Run:  python3 deploy/measure.py [trials]      (stack must already be up)
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

# Reuse the fault-injection + reset machinery verbatim so measurement and the
# RQ4 matrix drive the stack identically.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import faultmatrix as fm  # noqa: E402

PROJECT = fm.PROJECT
AGENT_CONTAINERS = {
    "payment-1": f"{PROJECT}-payment-agent-1",
    "subscription-1": f"{PROJECT}-subscription-agent-1",
    "auth-1": f"{PROJECT}-auth-agent-1",
    "user-api-1": f"{PROJECT}-user-api-agent-1",
}

DEFAULT_TRIALS = 3
POLL_TIMEOUT_S = 45.0
POLL_BATCH = 4
STATS_SAMPLES = 3


# -- observation helpers -----------------------------------------------------

def _fabric_tokens() -> tuple[int, int]:
    """Sum (prompt, completion) tokens across all agents' reasoner counters."""
    prompt = completion = 0
    for agent in fm.AGENTS:
        r = fm.state(agent).get("reasoner", {})
        prompt += int(r.get("prompt_tokens", 0) or 0)
        completion += int(r.get("completion_tokens", 0) or 0)
    return prompt, completion


def _reasoner_kind() -> str:
    return fm.state("payment-1").get("reasoner", {}).get("kind", "unknown")


def _sample_stats() -> dict[str, tuple[float, float]]:
    """Average CPU% and memory (MiB) per agent container over a few samples."""
    names = list(AGENT_CONTAINERS.values())
    cpu: dict[str, list[float]] = {n: [] for n in names}
    mem: dict[str, list[float]] = {n: [] for n in names}
    for _ in range(STATS_SAMPLES):
        proc = fm._run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}", *names],
            check=False,
        )
        for line in proc.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            name, cpu_s, mem_s = parts
            if name not in cpu:
                continue
            try:
                cpu[name].append(float(cpu_s.strip().rstrip("%")))
            except ValueError:
                pass
            mem[name].append(_parse_mem_mib(mem_s))
        time.sleep(1.0)
    out: dict[str, tuple[float, float]] = {}
    for name in names:
        c = statistics.fmean(cpu[name]) if cpu[name] else 0.0
        m = statistics.fmean(mem[name]) if mem[name] else 0.0
        out[name] = (c, m)
    return out


def _parse_mem_mib(mem_usage: str) -> float:
    """Parse the 'USED / LIMIT' left side of docker stats MemUsage into MiB."""
    used = mem_usage.split("/")[0].strip()
    num = "".join(ch for ch in used if ch.isdigit() or ch == ".")
    if not num:
        return 0.0
    value = float(num)
    unit = used[len(num):].strip().lower()
    if unit.startswith("gib"):
        return value * 1024.0
    if unit.startswith("mib"):
        return value
    if unit.startswith("kib"):
        return value / 1024.0
    if unit.startswith("b"):
        return value / (1024.0 * 1024.0)
    return value  # already MiB-ish


# -- one trial ---------------------------------------------------------------

def _time_to_disposition(inject, detected) -> tuple[float | None, str | None]:
    """Inject a fault, drive traffic, and time the first matching disposition.

    ``detected(states)`` returns ``(event_or_None)``: the first agent event that
    represents the expected disposition, or ``None`` if not yet observed.
    """
    t0 = time.time()
    inject()
    deadline = t0 + POLL_TIMEOUT_S
    while time.time() < deadline:
        fm.drive(POLL_BATCH)
        states = {name: fm.state(name) for name in fm.AGENTS}
        event = detected(states)
        if event is not None:
            return time.time() - t0, event.get("action")
        time.sleep(0.3)
    return None, None


def _first_event(states, agent, *, symptom=None, subject=None, disposition=None):
    for e in states.get(agent, {}).get("events", []):
        if symptom is not None and e.get("symptom") != symptom:
            continue
        if subject is not None and e.get("subject") != subject:
            continue
        if disposition is not None and e.get("disposition") != disposition:
            continue
        return e
    return None


def _systemic(states, agent, subject):
    s = states.get(agent, {}).get("subjects", {}).get(subject, {})
    if s.get("classification") == "systemic":
        return {"action": None, "fused": s.get("fused"),
                "reporters": s.get("reporters")}
    return None


# -- cases (each returns an injector + a detector) ---------------------------

def case_external_5xx():
    def inject():
        fm.stripe_fault("error500")

    def detect(states):
        return _first_event(states, "payment-1",
                             symptom="dependency_5xx", subject="stripe",
                             disposition="escalated_l3")

    return "external 5xx -> L3 escalation (payment)", inject, detect


def case_latency_spike():
    def inject():
        fm.stripe_fault("latency", 900)

    def detect(states):
        return _first_event(states, "payment-1",
                             symptom="latency_spike", subject="payment")

    return "latency spike -> L2 throttle (payment)", inject, detect


def case_pool_timeout():
    def inject():
        fm.compose("pause", "postgres-primary")

    def detect(states):
        return _systemic(states, "payment-1", "payment")

    return "pool timeout -> systemic consensus", inject, detect


CASES = [case_external_5xx, case_latency_spike, case_pool_timeout]


# -- driver ------------------------------------------------------------------

def _agg(values: list[float]) -> str:
    xs = [v for v in values if v is not None]
    if not xs:
        return "n/a"
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return f"{mean:6.2f} +/- {sd:4.2f}  [{min(xs):.2f}, {max(xs):.2f}]"


def main() -> int:
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TRIALS
    # The reasoner the harness will *enforce* on every reset via compose env
    # interpolation; this is authoritative, unlike a pre-reset /state read which
    # would reflect whatever the stack happened to be brought up with.
    kind = os.environ.get("CAF_AGENT_REASONER", "deterministic")
    print("=" * 78)
    print(f"CAF Phase E -- real measurement  (trials={trials}, reasoner={kind})")
    print("=" * 78)

    summary: list[tuple[str, dict]] = []
    for make in CASES:
        name, inject, detect = make()
        print(f"\n[case] {name}")
        lat: list[float] = []
        ptok: list[float] = []
        ctok: list[float] = []
        actions: list[str] = []
        cpu_tot: list[float] = []
        mem_tot: list[float] = []
        for t in range(1, trials + 1):
            fm.reset()  # clean, zeroed baseline (also restarts agents -> tokens=0)
            p0, c0 = _fabric_tokens()
            latency, action = _time_to_disposition(inject, detect)
            p1, c1 = _fabric_tokens()
            stats = _sample_stats()
            cpu = sum(v[0] for v in stats.values())
            mem = sum(v[1] for v in stats.values())
            dp, dc = p1 - p0, c1 - c0
            lat_s = f"{latency:.2f}s" if latency is not None else "MISS"
            act_s = action or "-"
            print(f"  trial {t}: t2disp={lat_s:>7}  tokens(p/c)={dp}/{dc}  "
                  f"action={act_s}  agentCPU={cpu:.1f}%  agentMem={mem:.0f}MiB")
            if latency is not None:
                lat.append(latency)
            ptok.append(dp)
            ctok.append(dc)
            if action:
                actions.append(action)
            cpu_tot.append(cpu)
            mem_tot.append(mem)
        summary.append((name, {
            "latency": lat, "ptok": ptok, "ctok": ctok,
            "cpu": cpu_tot, "mem": mem_tot, "actions": actions,
            "hits": len(lat), "trials": trials,
        }))
    fm.reset()

    print("\n" + "=" * 78)
    print("SUMMARY  (mean +/- stdev [min, max])")
    print("=" * 78)
    for name, d in summary:
        print(f"\n{name}   ({d['hits']}/{d['trials']} detected)")
        print(f"  time-to-disposition (s): {_agg(d['latency'])}")
        print(f"  prompt tokens          : {_agg(d['ptok'])}")
        print(f"  completion tokens      : {_agg(d['ctok'])}")
        print(f"  agent CPU total (%)    : {_agg(d['cpu'])}")
        print(f"  agent mem total (MiB)  : {_agg(d['mem'])}")
        if d["actions"]:
            print(f"  applied repair action  : {d['actions'][-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

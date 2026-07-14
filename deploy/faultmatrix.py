#!/usr/bin/env python3
"""Phase C -- real fault-injection matrix for RQ4 (resilience).

Each case induces a *genuine* fault in the running Docker testbed, drives real
traffic through the entry service, then reads each agent's live ``/state`` over
HTTP and asserts the expected diagnosis/consensus. Nothing is simulated and no
metric is fabricated: the harness only observes what the agents actually decided.

Faults are induced only via legitimate infrastructure controls (pause/kill a
container, sever a network link) or via the stripe *stub's* own fault plane --
never by editing an instrumented service -- so the statically compiled RST is
untouched throughout.

Run:  python3 deploy/faultmatrix.py        (stack must already be up)
Exit: 0 iff every case meets its expectation.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

COMPOSE = str(Path(__file__).resolve().parent / "docker-compose.yml")
PROJECT = "caf-testbed"
NETWORK = f"{PROJECT}_caf"

ENTRY = "http://localhost:8080/signup"
STRIPE_FAULT = "http://localhost:9100/admin/fault"
AGENTS = {
    "payment-1": "http://localhost:7071/state",
    "subscription-1": "http://localhost:7072/state",
    "auth-1": "http://localhost:7073/state",
    "user-api-1": "http://localhost:7074/state",
}

SERVICE_NAMES = ["payment", "subscription", "auth", "user-api"]
AGENT_NAMES = ["payment-agent", "subscription-agent", "auth-agent", "user-api-agent"]
SETTLE_S = 6.0


# -- infrastructure helpers --------------------------------------------------

def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["docker", "compose", "-f", COMPOSE, *args], check=check)


def container_id(service: str) -> str:
    return compose("ps", "-q", service).stdout.strip()


def net_disconnect(service: str) -> None:
    _run(["docker", "network", "disconnect", NETWORK, container_id(service)], check=False)


def net_connect(service: str) -> None:
    _run(["docker", "network", "connect", NETWORK, container_id(service)], check=False)


# -- http helpers ------------------------------------------------------------

def http(url: str, body=None, method: str = "POST", timeout: float = 10.0):
    data = json.dumps(body).encode() if body is not None else b"" if method == "POST" else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None, None


def signup():
    return http(ENTRY)[0]


def drive(n: int) -> None:
    for _ in range(n):
        signup()


def state(agent: str) -> dict:
    _, body = http(AGENTS[agent], method="GET")
    return body or {}


def stripe_fault(mode: str, latency_ms: int = 0) -> None:
    http(STRIPE_FAULT, {"mode": mode, "latency_ms": latency_ms})


# -- lifecycle ---------------------------------------------------------------

def wait_healthy(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if signup() == 200:
            return True
        time.sleep(1.0)
    return False


def reset() -> None:
    """Return the stack to a clean, healthy baseline between cases.

    Telemetry counters are monotonic, so a single boot-time transient would
    permanently mark a service as faulty and could be gossiped before a case
    even starts. To guarantee an independent trial we: (1) stop the agents so
    nothing scrapes during boot, (2) restart the services and confirm the chain
    is healthy, (3) restart the services once more to ZERO the counters now that
    we know they are reachable, driving no traffic afterwards, then (4) start the
    agents against clean, healthy services. They observe zeroed telemetry and
    therefore hold no beliefs until the case injects its own fault.
    """
    compose("up", "-d", check=False)               # revive any killed container
    net_connect("subscription-agent")               # heal any partition
    compose("unpause", "postgres-primary", check=False)
    stripe_fault("none")                            # clear any stub fault

    compose("stop", *AGENT_NAMES, check=False)       # agents off: no scraping during boot
    compose("restart", *SERVICE_NAMES, check=False)
    time.sleep(SETTLE_S)
    if not wait_healthy():
        raise RuntimeError("stack did not return to healthy baseline")

    compose("restart", *SERVICE_NAMES, check=False)  # zero counters; drive no traffic after
    time.sleep(SETTLE_S)
    compose("start", *AGENT_NAMES, check=False)      # agents start against clean services
    time.sleep(SETTLE_S)
    stripe_fault("none")


def poll(predicate, *, seconds: float = 30.0, batch: int = 4):
    """Drive traffic and re-check ``predicate`` until it holds or time runs out."""
    deadline = time.time() + seconds
    last = (False, "no observation")
    while time.time() < deadline:
        drive(batch)
        states = {name: state(name) for name in AGENTS}
        last = predicate(states)
        if last[0]:
            return last
        time.sleep(1.0)
    return last


def _subject(states: dict, agent: str, subject: str) -> dict:
    return states.get(agent, {}).get("subjects", {}).get(subject, {})


def _events(states: dict, agent: str) -> list:
    return states.get(agent, {}).get("events", [])


# -- cases -------------------------------------------------------------------

def case_pool_timeout(states=None):
    """Postgres pause -> payment pool_timeout + subscription 5xx -> SYSTEMIC."""
    compose("pause", "postgres-primary")

    def pred(s):
        p = _subject(s, "payment-1", "payment")
        reporters = set(p.get("reporters", []))
        ok = (p.get("classification") == "systemic"
              and {"payment-1", "subscription-1"} <= reporters)
        return ok, f"payment fused={p.get('fused')} reporters={sorted(reporters)} class={p.get('classification')}"

    return poll(pred)


def case_dependency_5xx(states=None):
    """Stripe 500 -> payment localizes external `stripe` and ESCALATES (L3)."""
    stripe_fault("error500")

    def pred(s):
        stripe = _subject(s, "payment-1", "stripe")
        esc = [e for e in _events(s, "payment-1")
               if e.get("subject") == "stripe" and e.get("disposition") == "escalated_l3"]
        sub_payment = _subject(s, "subscription-1", "payment")
        ok = bool(stripe) and bool(esc) and bool(sub_payment)
        return ok, (f"payment->stripe class={stripe.get('classification')} escalations={len(esc)} "
                    f"subscription->payment reporters={sub_payment.get('reporters')}")

    return poll(pred)


def case_latency_spike(states=None):
    """Stripe +900ms -> payment observes latency_spike -> L2 throttle."""
    stripe_fault("latency", 900)

    def pred(s):
        spikes = [e for e in _events(s, "payment-1")
                  if e.get("symptom") == "latency_spike" and e.get("subject") == "payment"]
        ok = bool(spikes)
        action = spikes[-1].get("action") if spikes else None
        return ok, f"payment latency_spike events={len(spikes)} action={action}"

    return poll(pred, seconds=40.0, batch=6)


def case_instance_kill(states=None):
    """Kill payment -> its caller (subscription) detects dependency_timeout."""
    compose("kill", "payment")

    def pred(s):
        events = [e for e in _events(s, "subscription-1")
                  if e.get("symptom") == "dependency_timeout" and e.get("subject") == "payment"]
        subj = _subject(s, "subscription-1", "payment")
        ok = bool(events)
        return ok, (f"subscription detected={len(events)} subject=payment "
                    f"class={subj.get('classification')} reporters={subj.get('reporters')}")

    return poll(pred)


def case_partition(states=None):
    """Same pool fault as case 1, but subscription-agent is network-partitioned:
    corroboration cannot form, so payment stays ISOLATED (contrast with case 1)."""
    net_disconnect("subscription-agent")
    compose("pause", "postgres-primary")

    def pred(s):
        p = _subject(s, "payment-1", "payment")
        reporters = set(p.get("reporters", []))
        # payment observes its own pool fault but never hears subscription's
        # corroborating belief -> one reporter, isolated.
        ok = (p.get("classification") == "isolated"
              and reporters == {"payment-1"})
        return ok, f"payment reporters={sorted(reporters)} class={p.get('classification')}"

    return poll(pred)


CASES = [
    ("pool_timeout -> systemic consensus", case_pool_timeout),
    ("external 5xx -> grounded L3 escalation", case_dependency_5xx),
    ("latency spike -> L2 throttle", case_latency_spike),
    ("instance kill -> caller detects timeout", case_instance_kill),
    ("network partition -> no false systemic", case_partition),
]


def main() -> int:
    print("=" * 74)
    print("CAF Phase C -- real fault-injection matrix (RQ4)")
    print("=" * 74)
    results = []
    for name, fn in CASES:
        print(f"\n[case] {name}")
        reset()
        try:
            passed, detail = fn()
        except Exception as exc:  # noqa: BLE001 - report, do not crash the matrix
            passed, detail = False, f"harness error: {type(exc).__name__}: {exc}"
        mark = "PASS" if passed else "FAIL"
        print(f"  -> {mark}: {detail}")
        results.append((name, passed, detail))
    reset()

    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    width = max(len(n) for n, _, _ in results)
    for name, passed, _ in results:
        print(f"  {name.ljust(width)}  {'PASS' if passed else 'FAIL'}")
    passed_n = sum(1 for _, p, _ in results if p)
    print(f"\n  {passed_n}/{len(results)} cases passed")
    return 0 if passed_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

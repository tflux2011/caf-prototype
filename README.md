# CAF Reference Implementation

**A runnable reference prototype for the Cognitive Autonomic Framework (CAF).**

Tobi Adeosun · Independent Researcher (Texas, USA) · [me@tadeosun.com](mailto:me@tadeosun.com)

This repository is the reference implementation and evaluation harness for the
CAF paper. The paper (LaTeX source) lives in a companion repository:
**[tflux2011/caf](https://github.com/tflux2011/caf)**, and the archived paper is
published on Zenodo: **[zenodo.org/records/21352597](https://zenodo.org/records/21352597)**
(DOI [10.5281/zenodo.21352597](https://doi.org/10.5281/zenodo.21352597)).

Everything here is real: a static RST compiler that parses source without
executing it, four instrumented microservices plus two external dependencies as
separate containers, independent per-service sidecar agents that gossip beliefs
over real HTTP/TCP, and a diagnostic reasoner that is deterministic by default
and swappable for a hosted language model. No results are simulated or
projected.

---

## What CAF is

CAF makes a distributed application **self-modeling** so it can reason about its
own failures instead of asking a centralized model to reconstruct system
structure from logs. The building blocks implemented here:

- **Runtime Semantic Topology (RST)** — a compact, versioned, executable
  self-model compiled *statically* from service source: dependency edges, failure
  domains, retry/timeout policy, criticality, and a **closed registry of
  permitted repair actions** per node.
- **Belief-sharing gossip fabric** — agents exchange confidence-weighted
  *diagnostic hypotheses* (not raw telemetry) and fuse them to tell *isolated*
  faults apart from *systemic* ones.
- **Tiered triage** — reflexive containment (T1) → node-local reasoning (T2) →
  global reasoning (T3), escalating only when confidence or authority is
  insufficient.
- **Verified-admission boundary** — a repair reaches "production" only if it is
  in the node's permitted-action registry *and* passes verification. This is
  re-imposed in code **after** the reasoner returns, so no reasoner
  (deterministic or hosted model) can cause an inadmissible repair.

## Repository layout

```
caf/
  annotations.py        developer-annotation overlay for the RST
  schema.py             pydantic models for the RST artifact and beliefs
  rst_compiler/         static AST → RST compiler (compiler, extractor, CLI)
  fabric/               gossip fabric: belief, fusion, gossip transport, node
  agent/                sidecar agent: signals, diagnosis, local triage, repair
  reason/               swappable diagnostic reasoner (OpenAI-backed option)
  runtime/              telemetry, instrumentation, observer, agent entrypoint
  eval/                 evaluation harnesses (RQ1, RQ1-LLM, RQ2, ablation)
deploy/                 Dockerfile, docker-compose testbed, fault injectors
testbed/subscription/   the four instrumented services (user-api → payment)
tests/                  test suite (offline; no network or API key required)
rst.json                a compiled RST artifact for the testbed topology
pyproject.toml          packaging, entry points, optional dependency groups
```

## Installation

Requires Python ≥ 3.10.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,runtime,reason]"
```

Optional dependency groups (install only what you need):

| Group | Purpose |
|-------|---------|
| `dev` | test runner (`pytest`) |
| `runtime` | to *run* the testbed services (FastAPI, httpx, psycopg, uvicorn) |
| `reason` | the hosted-model reasoner (`httpx`; the API key is read from an env var, no SDK) |

> The RST compiler and the full test suite need none of the `runtime`/`reason`
> extras—extraction is purely static and the tests are offline.

## Quick start

**Compile an RST from source:**

```sh
caf-rst testbed/subscription -o rst.json     # compile the testbed topology
caf-rst --print-schema                        # emit the RST JSON Schema
```

**Run the test suite (offline, no API key):**

```sh
pytest
```

**Run the microservice testbed** (requires Docker/Colima and the `runtime`
extra). The running dependency graph is wired to match `rst.json` byte-for-byte:

```sh
docker compose -f deploy/docker-compose.yml up -d --build
# fault injection + measurement:
python deploy/faultmatrix.py     # inject the five-fault matrix
python deploy/measure.py         # collect latency / token / footprint metrics
docker compose -f deploy/docker-compose.yml down --remove-orphans
```

**Run the evaluation harnesses:**

```sh
caf-eval             # RQ1: RST grounding (deterministic reasoner)
caf-eval-rq1-llm     # RQ1 with a hosted model (needs OPENAI_API_KEY)
caf-eval-rq2         # RQ2: isolated vs. systemic discrimination
caf-eval-ablation    # mechanism ablation matrix
```

## Selecting the reasoner

The diagnostic reasoner is chosen by environment variable; the deterministic,
RST-grounded playbook is the default and needs no external service:

```sh
export CAF_AGENT_REASONER=deterministic   # default
# or, to use a hosted model:
export CAF_AGENT_REASONER=openai
export CAF_OPENAI_MODEL=gpt-4o-mini        # or gpt-4o
export OPENAI_API_KEY=...                   # read from the shell only — see Security
```

## Security notes

- **No secrets in the repo or image.** The hosted-model API key is read from the
  `OPENAI_API_KEY` environment variable at runtime only; it is never stored,
  logged, or committed. `.env`, `*.pem`, and `*.key` files are gitignored.
- **The testbed is for local/CI use only.** The Postgres data tier runs with
  trust auth on an internal-only network and is not published to the host; the
  fault-injection control plane carries no secrets and must never run outside a
  local or CI testbed.
- **Bounded autonomy by construction.** Agents have no raw shell or arbitrary API
  access; they may only select from a node's closed permitted-action set, and
  every candidate passes the verified-admission boundary before it is applied.

## Preliminary results (honest scope)

Measured on a single six-node topology, one container host, a handful of fault
types, and N = 5 trials per fault:

- **RST grounding (RQ1)** roughly *doubled* correct root-cause localization
  (grounded 5/6 vs. ungrounded 2/6), with identical results for `gpt-4o-mini`
  and `gpt-4o`—the benefit came from the self-model, not model scale. A grounded
  consultation costs a few hundred tokens.
- **Discrimination + bounded repair (RQ2, RQ5)**: pool exhaustion was correctly
  classified *systemic* only after a quorum agreed; an external-dependency 5xx
  was *escalated* rather than repaired (no permitted local action); a partitioned
  agent produced no false systemic escalation.
- **Overhead (RQ4)**: four-agent footprint ~4–6% CPU and ~149–153 MiB; the hosted
  model's latency is only visible when it lies on the critical path.

These establish that the mechanisms work end-to-end on real infrastructure—not
that CAF is validated at fleet scale. RQ3 (tiering economics at scale) and RQ6
(drift robustness) remain open, and the deterministic reasoner is the honest
default baseline. See the paper's *Threats to validity* discussion for the
findings these runs surfaced.

## Citation

This software accompanies the CAF paper. To cite the paper (archived on Zenodo):

```bibtex
@misc{adeosun2026caf,
  title        = {Cognitive Autonomic Frameworks: Layered Cognition for
                  Self-Modeling, Self-Healing Distributed Systems},
  author       = {Adeosun, Tobi},
  year         = {2026},
  note         = {Preprint},
  doi          = {10.5281/zenodo.21352597},
  howpublished = {\url{https://zenodo.org/records/21352597}}
}
```

To cite the software itself, use the metadata in [`CITATION.cff`](CITATION.cff)
(GitHub renders a "Cite this repository" button from it).

## License

Licensed under the [Apache License 2.0](LICENSE).

## Contact

Tobi Adeosun — [me@tadeosun.com](mailto:me@tadeosun.com)

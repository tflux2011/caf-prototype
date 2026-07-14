"""The Runtime Semantic Topology (RST) data model.

This module is the single, language-agnostic contract for the RST artifact.
Every extractor front-end (the Python AST compiler here, a future TypeScript
``ts-morph`` front-end, etc.) must emit JSON that validates against these
models. A JSON Schema can be derived from them (see ``RST.model_json_schema``)
so that non-Python front-ends can target the identical contract.

The schema mirrors Listing 1 of the paper.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Criticality(str, Enum):
    """Business criticality of a semantic unit (a developer-declared policy)."""

    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class EdgeKind(str, Enum):
    """The transport class of a dependency edge, inferred from code."""

    http = "http"
    sql = "sql"
    grpc = "grpc"
    message = "message"
    cache = "cache"


class Provenance(str, Enum):
    """How an edge entered the graph.

    ``compiled`` edges come from static analysis, ``declared`` edges from
    developer annotations, and ``observed`` edges from runtime reconciliation.
    Provenance is what keeps the merge auditable (paper, Sec. RST generation).
    """

    compiled = "compiled"
    declared = "declared"
    observed = "observed"


# The closed registry of permitted repair actions known to this compiler
# version. Agents treat any action outside their known set as inadmissible
# (the paper's forward-compatibility rule), so unknown strings are allowed in
# the artifact but flagged by tooling rather than rejected here.
KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        "apply_traffic_throttle",
        "restart_connection_pool",
        "trigger_canary_rollback",
        "open_circuit_breaker",
        "scale_consumers",
        "shed_load",
    }
)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max: int = Field(ge=0)
    backoff_ms: int = Field(ge=0)
    jitter: bool = False


class LatencyEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p50: Optional[int] = Field(default=None, ge=0)
    p99: Optional[int] = Field(default=None, ge=0)


class Node(BaseModel):
    """A vertex of the RST: a service or an external dependency."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    kind: Literal["service", "external_dependency"] = "service"
    endpoints: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    failure_domain: Optional[str] = None
    ownership: Optional[str] = None
    criticality: Optional[Criticality] = None
    retry_policy: Optional[RetryPolicy] = None
    expected_latency_ms: Optional[LatencyEnvelope] = None
    # A *closed* set of permitted repairs. External dependencies carry the
    # empty set: an agent cannot repair them and must escalate.
    permitted_actions: list[str] = Field(default_factory=list)


class Edge(BaseModel):
    """A directed dependency edge (call, query, or message flow)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=True)

    from_: str = Field(alias="from")
    to: str
    kind: EdgeKind
    pool: Optional[str] = None
    timeout_ms: Optional[int] = Field(default=None, ge=0)
    idempotent: Optional[bool] = None
    provenance: Provenance = Provenance.compiled


class RST(BaseModel):
    """The Runtime Semantic Topology: a directed, attributed graph."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str
    generated_by: str
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)

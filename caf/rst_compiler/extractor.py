"""Static AST extraction of a single service's semantic unit.

The extractor parses a service's Python source (it never imports or executes
it) and resolves:

* outbound HTTP calls (``httpx``/``requests`` module calls and client
  instances) into ``http`` dependency edges;
* database ``connect`` calls (``psycopg``/``psycopg2``/``asyncpg``) into ``sql``
  dependency edges;
* FastAPI-style route decorators into the node's endpoint list;
* the ``@caf_service`` policy annotation into node policy attributes.

Extraction is deliberately conservative and neither sound nor complete against
the true runtime topology (paper, "Extraction and its soundness boundary").
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from caf.schema import Criticality, EdgeKind, LatencyEnvelope, Node, RetryPolicy

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "request"}
IDEMPOTENT_METHODS = {"get", "head", "options"}
HTTP_LIBS = {"httpx", "requests"}
CLIENT_FACTORIES = {"Client", "AsyncClient", "Session"}
DB_LIBS = {"psycopg", "psycopg2", "asyncpg"}
ROUTER_NAMES = {"app", "router"}
CAF_DECORATOR = "caf_service"


@dataclass
class RawDep:
    """A dependency edge before external-node classification."""

    to: str
    kind: EdgeKind
    idempotent: Optional[bool] = None


@dataclass
class ServiceResult:
    node_id: str
    node: Node
    deps: list[RawDep] = field(default_factory=list)
    external_meta: dict[str, dict] = field(default_factory=dict)


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """Collect module-level ``NAME = "literal"`` bindings for URL resolution."""
    consts: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value
    return consts


def _client_vars(tree: ast.AST) -> set[str]:
    """Names bound to an HTTP client (e.g. ``c = httpx.Client()``)."""
    clients: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in HTTP_LIBS
                and func.attr in CLIENT_FACTORIES
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        clients.add(target.id)
    return clients


def _caf_meta(tree: ast.AST) -> Optional[dict]:
    """Read the first ``@caf_service(...)`` annotation found."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    func = dec.func
                    name = (
                        func.id
                        if isinstance(func, ast.Name)
                        else func.attr
                        if isinstance(func, ast.Attribute)
                        else None
                    )
                    if name == CAF_DECORATOR:
                        return _literal_kwargs(dec)
    return None


def _literal_kwargs(call: ast.Call) -> dict:
    """Safely evaluate a call's keyword args as literals (no code execution)."""
    out: dict = {}
    for kw in call.keywords:
        if kw.arg is None:
            continue
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError, TypeError):
            out[kw.arg] = None
    return out


def _resolve_str(node: ast.AST, consts: dict[str, str]) -> Optional[str]:
    """Best-effort resolution of a string expression to its literal value."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):  # f-string
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
                parts.append(consts.get(value.value.id, ""))
            else:
                parts.append("")
        return "".join(parts) or None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_str(node.left, consts) or ""
        right = _resolve_str(node.right, consts) or ""
        return (left + right) or None
    return None


def _host(url: str) -> Optional[str]:
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _endpoints(tree: ast.AST) -> list[str]:
    """Route paths from FastAPI-style ``@app.get("/path")`` decorators."""
    eps: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in HTTP_METHODS
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id in ROUTER_NAMES
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)
                ):
                    eps.append(dec.args[0].value)
    return eps


def _http_deps(tree: ast.AST, consts: dict[str, str], clients: set[str]) -> list[RawDep]:
    deps: list[RawDep] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        base = node.func.value
        if method not in HTTP_METHODS:
            continue
        if not (isinstance(base, ast.Name) and (base.id in HTTP_LIBS or base.id in clients)):
            continue
        url = _resolve_str(node.args[0], consts) if node.args else None
        if url is None:
            for kw in node.keywords:
                if kw.arg == "url":
                    url = _resolve_str(kw.value, consts)
        if not url:
            continue
        host = _host(url)
        if host:
            deps.append(
                RawDep(to=host, kind=EdgeKind.http, idempotent=method in IDEMPOTENT_METHODS)
            )
    return deps


def _db_deps(tree: ast.AST, consts: dict[str, str]) -> list[RawDep]:
    deps: list[RawDep] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (
            node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in DB_LIBS
        ):
            continue
        host: Optional[str] = None
        for kw in node.keywords:
            if kw.arg == "host":
                host = _resolve_str(kw.value, consts)
        if host is None and node.args:
            dsn = _resolve_str(node.args[0], consts)
            if dsn:
                host = _host(dsn)
        deps.append(RawDep(to=host or "database", kind=EdgeKind.sql))
    return deps


def extract_service(service_dir: Path) -> Optional[ServiceResult]:
    """Extract one service's node and dependency edges from its source tree.

    Returns ``None`` if the directory contains no ``@caf_service`` descriptor.
    """
    files = sorted(service_dir.rglob("*.py"))
    consts: dict[str, str] = {}
    clients: set[str] = set()
    meta: Optional[dict] = None
    endpoints: list[str] = []
    trees: list[ast.AST] = []

    for path in files:
        tree = _parse(path)
        if tree is None:
            continue
        trees.append(tree)
        consts.update(_string_constants(tree))
        clients |= _client_vars(tree)
        if meta is None:
            meta = _caf_meta(tree)
        endpoints.extend(_endpoints(tree))

    if meta is None or not meta.get("name"):
        return None

    deps: list[RawDep] = []
    for tree in trees:
        deps.extend(_http_deps(tree, consts, clients))
        deps.extend(_db_deps(tree, consts))

    return ServiceResult(
        node_id=meta["name"],
        node=_build_node(meta, endpoints, deps),
        deps=deps,
        external_meta=meta.get("external_deps") or {},
    )


def _build_node(meta: dict, endpoints: list[str], deps: list[RawDep]) -> Node:
    retry = meta.get("retry")
    latency = meta.get("expected_latency_ms")
    criticality = meta.get("criticality")
    return Node(
        kind="service",
        endpoints=sorted(set(endpoints)),
        depends_on=sorted({dep.to for dep in deps}),
        failure_domain=meta.get("failure_domain"),
        ownership=meta.get("ownership"),
        criticality=Criticality(criticality) if criticality else None,
        retry_policy=RetryPolicy(**retry) if isinstance(retry, dict) else None,
        expected_latency_ms=LatencyEnvelope(**latency) if isinstance(latency, dict) else None,
        permitted_actions=list(meta.get("permitted_actions") or []),
    )

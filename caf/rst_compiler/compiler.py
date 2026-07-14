"""Whole-repository RST compilation.

Discovers service directories, extracts each into a node with its dependency
edges, then merges them: internal services become ``service`` nodes and any
dependency target that is not an internal service becomes an
``external_dependency`` node with an empty (closed) permitted-action set.
"""

from __future__ import annotations

from pathlib import Path

from caf.rst_compiler.extractor import ServiceResult, extract_service
from caf.schema import RST, Criticality, Edge, Node


def _discover_service_dirs(root: Path) -> list[Path]:
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and any(child.rglob("*.py"))
    )


def compile_rst(root: Path, *, version: str, generated_by: str) -> RST:
    results: list[ServiceResult] = []
    for service_dir in _discover_service_dirs(root):
        result = extract_service(service_dir)
        if result is not None:
            results.append(result)

    internal_ids = {result.node_id for result in results}
    nodes: dict[str, Node] = {}
    external_nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    for result in results:
        nodes[result.node_id] = result.node
        for dep in result.deps:
            dep_meta = result.external_meta.get(dep.to, {})
            edges.append(
                Edge.model_validate(
                    {
                        "from": result.node_id,
                        "to": dep.to,
                        "kind": dep.kind.value,
                        "idempotent": dep.idempotent,
                        "pool": dep_meta.get("pool"),
                        "timeout_ms": dep_meta.get("timeout_ms"),
                        "provenance": "compiled",
                    }
                )
            )
            if dep.to not in internal_ids and dep.to not in external_nodes:
                criticality = dep_meta.get("criticality")
                external_nodes[dep.to] = Node(
                    kind="external_dependency",
                    failure_domain=dep_meta.get("failure_domain"),
                    criticality=Criticality(criticality) if criticality else None,
                    permitted_actions=[],
                )

    for node_id, node in external_nodes.items():
        nodes.setdefault(node_id, node)

    edges = _dedupe_edges(edges)
    return RST(version=version, generated_by=generated_by, nodes=nodes, edges=edges)


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Edge] = []
    for edge in edges:
        key = (edge.from_, edge.to, str(edge.kind))
        if key not in seen:
            seen.add(key)
            unique.append(edge)
    return unique

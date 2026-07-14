"""The ``@caf_service`` policy annotation.

Developers attach this to a service's descriptor to declare the *policy*
attributes that cannot be inferred from code alone: business criticality,
failure domain, ownership, and---critically---the closed permitted-action
registry. The RST compiler reads this annotation *statically* (via the AST); it
is never executed during extraction. It is also available at runtime for
introspection.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

CAF_META_ATTR = "__caf_meta__"


def caf_service(
    *,
    name: str,
    failure_domain: Optional[str] = None,
    criticality: Optional[str] = None,
    ownership: Optional[str] = None,
    permitted_actions: Optional[Sequence[str]] = None,
    depends_on: Optional[Sequence[str]] = None,
    retry: Optional[dict[str, Any]] = None,
    expected_latency_ms: Optional[dict[str, int]] = None,
    external_deps: Optional[dict[str, dict[str, Any]]] = None,
) -> Callable[[Any], Any]:
    """Declare CAF policy for the decorated service descriptor.

    Args:
        name: The RST node id for this service (must match the hostname peers
            use to reach it, so edges resolve to this node).
        failure_domain: The failure-domain label used to aggregate beliefs.
        criticality: One of ``low``/``medium``/``high``/``critical``.
        ownership: Owning team.
        permitted_actions: The closed set of repairs an agent may apply.
        depends_on: Optional explicit dependency hints.
        retry: Retry policy ``{"max", "backoff_ms", "jitter"}``.
        expected_latency_ms: Latency envelope ``{"p50", "p99"}``.
        external_deps: Per-external-dependency policy the consuming service
            declares (e.g. Postgres ``failure_domain``/``criticality`` and the
            edge ``pool``/``timeout_ms``).
    """
    meta = {
        "name": name,
        "failure_domain": failure_domain,
        "criticality": criticality,
        "ownership": ownership,
        "permitted_actions": list(permitted_actions or []),
        "depends_on": list(depends_on or []),
        "retry": retry,
        "expected_latency_ms": expected_latency_ms,
        "external_deps": external_deps or {},
    }

    def decorate(obj: Any) -> Any:
        setattr(obj, CAF_META_ATTR, meta)
        return obj

    return decorate

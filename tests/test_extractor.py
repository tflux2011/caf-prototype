from pathlib import Path

from caf.rst_compiler.compiler import compile_rst

TESTBED = Path(__file__).resolve().parents[1] / "testbed" / "subscription"


def _rst():
    return compile_rst(TESTBED, version="0.1.0", generated_by="pytest")


def test_all_services_extracted():
    rst = _rst()
    for service in ("user-api", "auth", "subscription", "payment"):
        assert service in rst.nodes, f"missing service node {service}"
        assert rst.nodes[service].kind == "service"


def test_payment_policy_attributes():
    pay = _rst().nodes["payment"]
    assert pay.criticality == "critical"
    assert pay.failure_domain == "billing"
    assert pay.ownership == "team-payments"
    assert set(pay.permitted_actions) == {
        "apply_traffic_throttle",
        "restart_connection_pool",
        "trigger_canary_rollback",
        "open_circuit_breaker",
    }
    assert pay.retry_policy is not None
    assert pay.retry_policy.max == 3
    assert pay.expected_latency_ms.p99 == 250


def test_external_dependencies_have_empty_action_set():
    rst = _rst()
    assert rst.nodes["postgres-primary"].kind == "external_dependency"
    assert rst.nodes["postgres-primary"].permitted_actions == []
    assert rst.nodes["postgres-primary"].failure_domain == "data-tier"
    assert rst.nodes["stripe"].kind == "external_dependency"
    assert rst.nodes["stripe"].permitted_actions == []


def test_call_chain_edges():
    edges = {(e.from_, e.to, str(e.kind)) for e in _rst().edges}
    assert ("user-api", "auth", "http") in edges
    assert ("auth", "subscription", "http") in edges
    assert ("subscription", "payment", "http") in edges
    assert ("payment", "postgres-primary", "sql") in edges
    assert ("payment", "stripe", "http") in edges


def test_sql_edge_carries_pool_and_timeout():
    sql_edges = [e for e in _rst().edges if str(e.kind) == "sql"]
    assert len(sql_edges) == 1
    edge = sql_edges[0]
    assert edge.pool == "billing-pool"
    assert edge.timeout_ms == 500

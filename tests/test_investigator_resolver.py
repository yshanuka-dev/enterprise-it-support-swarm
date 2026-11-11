"""Unit and integration tests for Day 11: L2 Investigator and L3 Resolver nodes."""

from tests.test_graph import run

from incident_swarm.app.graph import build_graph
from incident_swarm.app.nodes import Nodes, Services, default_metrics_tool, default_runbook_search


def test_default_metrics_tool_retrieves_telemetry():
    """Verify default_metrics_tool retrieves metrics and anomaly list for a service."""
    result = default_metrics_tool("redis-cluster")
    assert result["service"] == "redis-cluster"
    assert "metrics" in result
    assert isinstance(result["anomalies"], list)


def test_default_runbook_search_finds_relevant_sections():
    """Verify default_runbook_search finds markdown runbooks matching incident symptoms."""
    hits = default_runbook_search("Redis out of memory allkeys-lru")
    assert len(hits) > 0
    assert hits[0]["runbook_id"] == "RB-REDIS-OOM"
    assert "section" in hits[0]
    assert hits[0]["score"] >= 0.35


def test_l2_investigator_success_and_evidence():
    """Verify l2_investigator attaches telemetry evidence to state."""
    nodes = Nodes(Services(), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "service": "redis-cluster",
        "ticket_type": "critical_outage",
        "path": ["intake", "l1_triage"],
    }
    update = nodes.l2_investigator(state)
    assert update["tool_error"] is None
    assert "metrics" in update["evidence"]
    assert "l2_investigator" in update["path"]


def test_l2_investigator_counts_retries_on_failure():
    """Verify tool failures increment tool_retry_count without throwing exceptions."""
    def faulty_tool(_):
        raise ConnectionResetError("Observability endpoint timed out")

    nodes = Nodes(Services(metrics_tool=faulty_tool), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "service": "auth-api",
        "tool_retry_count": 1,
        "path": ["intake", "l1_triage"],
    }
    update = nodes.l2_investigator(state)
    assert update["tool_retry_count"] == 2
    assert "ConnectionResetError" in update["tool_error"]


def test_l3_resolver_crag_rejection_escalates_to_human():
    """Verify CRAG gate: when no runbook meets score threshold, escalates to human."""
    nodes = Nodes(Services(runbook_search=lambda _: []), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-CRAG",
        "ticket_type": "critical_outage",
        "severity": "critical",
        "service": "custom-obscure-service",
        "messages": [("user", "Unrecognized obscure error code 99999")],
        "path": ["intake", "l1_triage", "l2_investigator"],
    }
    update = nodes.l3_resolver(state)
    assert update["escalated_to_human"] is True
    assert update["escalation_reason"] == "no matching runbook found"
    assert update["needs_voice_escalation"] is True
    assert update["runbook_citations"] == []


def test_l3_resolver_clearance_granted_for_devops():
    """Verify clearance gating permits remediation for authorized DevOps employee."""
    hit = [{"runbook_id": "RB-REDIS-OOM", "section": "Remediation", "score": 0.85}]
    nodes = Nodes(Services(runbook_search=lambda _: hit), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-DEV1",
        "user_id": "EMP-0012",
        "ticket_type": "critical_outage",
        "severity": "critical",
        "service": "prod-cache-01",
        "messages": [("user", "Redis OOM error")],
        "path": ["intake", "l1_triage", "l2_investigator"],
    }
    update = nodes.l3_resolver(state)
    assert update["clearance_ok"] is True
    assert "RB-REDIS-OOM" in update["reply"]
    assert "Clearance verified" in update["reply"]
    assert "Follow RB-REDIS-OOM > Remediation" in update["proposed_action"]


def test_l3_resolver_clearance_denied_for_unprivileged_employee():
    """Verify clearance gating denies privileged restart for employee without clearance."""
    hit = [{"runbook_id": "RB-PG-CONNECTIONS", "section": "Remediation", "score": 0.85}]
    nodes = Nodes(Services(runbook_search=lambda _: hit), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-FIN1",
        "user_id": "EMP-0001",
        "ticket_type": "critical_outage",
        "severity": "critical",
        "service": "postgres-primary",
        "messages": [("user", "Postgres connection pool leak")],
        "path": ["intake", "l1_triage", "l2_investigator"],
    }
    update = nodes.l3_resolver(state)
    assert update["clearance_ok"] is False
    assert "lacks DevOps clearance" in update["reply"]
    assert update.get("escalated_to_human") is not True  # routed to on-call DevOps approval


def test_end_to_end_redis_outage_investigation_and_resolution():
    """Verify end-to-end flow from user message to L2 evidence and L3 runbook citation."""
    graph = build_graph()
    state = run(graph, "EMP-0012: Redis is running out of memory on prod-cache-01", session="redis-investigate")
    assert state["ticket_type"] == "critical_outage"
    assert "l2_investigator" in state["path"]
    assert "l3_resolver" in state["path"]
    assert "writeback" in state["path"]
    assert state["needs_voice_escalation"] is True
    assert len(state["runbook_citations"]) > 0
    assert "RB-REDIS-OOM" in state["reply"]

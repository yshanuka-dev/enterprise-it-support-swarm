"""Day 16 tests: async graph bridge and Type 4 voice escalation.

Verifies:
1. Graph invoker registration and dispatch.
2. In-memory result store (investigating → completed).
3. escalate_critical_outage with cleared user (restart succeeds).
4. escalate_critical_outage with denied user (clearance denied).
5. get_investigation_status reads back graph results.
6. get_voice_tools now includes 4 tools.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from incident_swarm.voice.tools import (
    _read_result,
    _store_result,
    escalate_critical_outage,
    get_investigation_status,
    get_voice_tools,
    report_incident,
    set_graph_invoker,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async function synchronously for testing."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _call(tool, **kwargs):
    """Call the underlying async function of a FunctionTool."""
    return _run(tool._func(**kwargs))


@pytest.fixture(autouse=True)
def _clean_results():
    """Clear the in-memory graph result store between tests."""
    from incident_swarm.voice.tools import _graph_results
    _graph_results.clear()
    set_graph_invoker(None)
    yield
    _graph_results.clear()
    set_graph_invoker(None)


# ── Test 1: Voice tools list now has 4 tools ─────────────────────────────────

def test_voice_tools_count():
    """Day 16: get_voice_tools returns 4 tools (added escalate_critical_outage)."""
    tools = get_voice_tools()
    assert len(tools) == 4
    names = {t.info.name for t in tools}
    assert "report_incident" in names
    assert "get_investigation_status" in names
    assert "quick_metrics" in names
    assert "escalate_critical_outage" in names


# ── Test 2: In-memory result store ───────────────────────────────────────────

def test_result_store_roundtrip():
    """Thread-safe store and read of graph results."""
    _store_result("TKT-001", {"status": "investigating"})
    r = _read_result("TKT-001")
    assert r is not None
    assert r["status"] == "investigating"

    _store_result("TKT-001", {"status": "completed", "state": {"reply": "fixed"}})
    r = _read_result("TKT-001")
    assert r["status"] == "completed"
    assert r["state"]["reply"] == "fixed"

    assert _read_result("TKT-MISSING") is None


# ── Test 3: report_incident dispatches background task ───────────────────────

def test_report_incident_dispatches():
    """report_incident creates a ticket and dispatches background investigation."""
    mock_invoker = MagicMock()
    set_graph_invoker(mock_invoker)

    with patch("incident_swarm.voice.tools.ticketing.create_ticket") as mock_create:
        mock_create.return_value = {"ticket_id": "TKT-VOICE-123"}
        result = _call(report_incident, summary="Redis is OOM", service="redis-cluster")

    assert "TKT-VOICE-123" in result
    assert "investigation" in result.lower() or "swarm" in result.lower()


# ── Test 4: get_investigation_status reads graph result ──────────────────────

def test_get_investigation_status_investigating():
    """When graph is still running, return 'still investigating'."""
    _store_result("TKT-002", {"status": "investigating"})
    result = _call(get_investigation_status, ticket_id="TKT-002")
    assert "investigating" in result.lower() or "still" in result.lower()


def test_get_investigation_status_completed():
    """When graph has completed, return the resolution details."""
    _store_result("TKT-003", {
        "status": "completed",
        "state": {
            "reply": "Service restarted",
            "severity": "critical",
            "path": ["intake", "l1_triage", "l2_investigator", "l3_resolver", "writeback"],
            "resolution_summary": "Restarted redis-cluster via RB-REDIS-OOM",
            "escalated_to_human": False,
        },
    })
    result = _call(get_investigation_status, ticket_id="TKT-003")
    assert "complete" in result.lower()
    assert "redis" in result.lower() or "restart" in result.lower()


def test_get_investigation_status_error():
    """When graph errored, return the error status."""
    _store_result("TKT-004", {"status": "error", "error": "Graph invocation failed"})
    result = _call(get_investigation_status, ticket_id="TKT-004")
    assert "error" in result.lower() or "manual" in result.lower()


# ── Test 5: escalate_critical_outage — cleared ───────────────────────────────

def test_escalate_critical_outage_cleared():
    """Type 4: cleared operator gets a successful restart confirmation."""
    with (
        patch("incident_swarm.mcp_servers.ticketing.check_clearance") as mock_cl,
        patch("incident_swarm.mcp_servers.action.restart_service") as mock_rs,
    ):
        mock_cl.return_value = {"cleared": True}
        mock_rs.return_value = {
            "status": "success",
            "service": "auth-db",
            "audit_id": "AUD-777",
            "message": "Service restarted successfully",
        }
        result = _call(escalate_critical_outage, service="auth-db", operator_id="EMP-0012")

    assert "success" in result.lower() or "restarted" in result.lower()
    assert "auth-db" in result
    assert "AUD-777" in result


# ── Test 6: escalate_critical_outage — denied ────────────────────────────────

def test_escalate_critical_outage_denied():
    """Type 4: operator without clearance gets a denial message."""
    with patch("incident_swarm.mcp_servers.ticketing.check_clearance") as mock_cl:
        mock_cl.return_value = {"cleared": False, "reason": "Clearance level too low"}
        result = _call(escalate_critical_outage, service="auth-db", operator_id="EMP-9999")

    assert "denied" in result.lower() or "clearance" in result.lower()
    assert "EMP-9999" in result


# ── Test 7: escalate_critical_outage — dry run ───────────────────────────────

def test_escalate_critical_outage_dry_run():
    """Type 4 dry run: returns simulation result without affecting production."""
    with (
        patch("incident_swarm.mcp_servers.ticketing.check_clearance") as mock_cl,
        patch("incident_swarm.mcp_servers.action.restart_service") as mock_rs,
    ):
        mock_cl.return_value = {"cleared": True}
        mock_rs.return_value = {
            "status": "simulated",
            "service": "redis-cluster",
            "message": "Simulation OK",
        }
        result = _call(escalate_critical_outage, service="redis-cluster", operator_id="EMP-0012", dry_run=True)

    assert "dry run" in result.lower() or "simulated" in result.lower()

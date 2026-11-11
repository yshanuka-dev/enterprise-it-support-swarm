"""Unit and integration tests for Type 1 (Access) and Type 2 (Asset) Handlers."""

from tests.test_graph import run

from incident_swarm.app.graph import build_graph
from incident_swarm.app.nodes import Nodes, Services


def test_access_handler_cleared_devops_employee():
    """Verify access_handler grants clearance to verified employee."""
    nodes = Nodes(Services(), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-ACC1",
        "user_id": "EMP-0012",
        "ticket_type": "access_identity",
        "path": ["intake", "l1_triage"],
    }
    result = nodes.access_handler(state)
    assert result["clearance_ok"] is True
    assert "Clearance verified for EMP-0012" in result["reply"]
    assert "access_handler" in result["path"]


def test_access_handler_unauthorized_employee():
    """Verify access_handler denies clearance when permission check fails."""
    nodes = Nodes(Services(), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-ACC2",
        "user_id": "EMP-0001",
        "ticket_type": "access_identity",
        "path": ["intake", "l1_triage"],
    }
    result = nodes.access_handler(state)
    assert result["clearance_ok"] is False
    assert "lacks clearance" in result["reply"]
    assert "routed to your division manager" in result["reply"]


def test_access_handler_anonymous_guidance():
    """Verify access_handler returns self-service instructions when no employee ID is provided."""
    nodes = Nodes(Services(), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-ACC3",
        "user_id": None,
        "ticket_type": "access_identity",
        "path": ["intake", "l1_triage"],
    }
    result = nodes.access_handler(state)
    assert result["clearance_ok"] is True
    assert "sso.internal/auth/reset" in result["reply"]


def test_asset_handler_hardware_replacement_generation():
    """Verify asset_handler checks inventory and initiates hardware RMA replacement."""
    nodes = Nodes(Services(), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-AST1",
        "user_id": "EMP-0089",
        "asset_id": "AST-0014",
        "ticket_type": "asset_provisioning",
        "path": ["intake", "l1_triage"],
        "messages": [("user", "Laptop screen cracked")],
    }
    result = nodes.asset_handler(state)
    assert result["rma_number"] is not None
    assert "RMA-AST-0014" in result["rma_number"]
    assert "IT Hardware Depot" in result["reply"]


def test_asset_handler_looks_up_asset_by_owner():
    """Verify asset_handler resolves asset tag from owner employee ID."""
    nodes = Nodes(Services(), {"max_hops": 8, "max_tool_retries": 3})
    state = {
        "ticket_id": "TKT-TEST-AST2",
        "user_id": "EMP-0089",
        "asset_id": None,
        "ticket_type": "asset_provisioning",
        "path": ["intake", "l1_triage"],
        "messages": [("user", "My laptop won't turn on")],
    }
    result = nodes.asset_handler(state)
    assert result["rma_number"] is not None
    assert result["asset_id"] == "AST-0014"


def test_graph_end_to_end_broken_laptop():
    """Verify end-to-end graph flow for broken laptop ticket generating RMA."""
    graph = build_graph()
    state = run(graph, "EMP-0089: My laptop screen has dead pixels on AST-0014", session="laptop-rma")
    assert "asset_handler" in state["path"]
    assert "writeback" in state["path"]
    assert state["ticket_type"] == "asset_provisioning"
    assert state.get("rma_number") is not None
    assert "RMA-AST-0014" in state["rma_number"]

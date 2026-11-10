"""Unit tests for Ticketing MCP Server tools."""

from __future__ import annotations

import pytest

from incident_swarm.mcp_servers.db import DatabaseManager
from incident_swarm.mcp_servers.ticketing import (
    check_clearance,
    create_ticket,
    get_ticket,
    lookup_asset,
    search_tickets,
    update_ticket,
)


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure each test runs with a fresh database manager instance."""
    import incident_swarm.mcp_servers.db as db_mod

    db_mod._db_manager = DatabaseManager(dsn=None)  # Force in-memory mode for tests
    yield
    db_mod._db_manager = None


class TestTicketingMCP:
    def test_get_ticket_existing(self):
        res = get_ticket("TKT-00001")
        assert res["status"] == "success"
        assert res["ticket"]["id"] == "TKT-00001"
        assert res["ticket"]["reported_by"] == "EMP-0408"
        assert res["ticket"]["ticket_type"] == "access_identity"

    def test_get_ticket_not_found(self):
        res = get_ticket("TKT-NONEXISTENT")
        assert "error" in res
        assert "not found" in res["error"].lower()

    def test_search_tickets_by_query(self):
        res = search_tickets(query="VPN")
        assert res["status"] == "success"
        assert res["count"] >= 1
        assert any("VPN" in t["issue_description"] for t in res["tickets"])

    def test_search_tickets_by_status(self):
        res = search_tickets(status="open")
        assert res["status"] == "success"
        assert all(t["status"] == "open" for t in res["tickets"])

    def test_create_ticket(self):
        res = create_ticket(
            reported_by="EMP-0010",
            issue_description="Redis connection timeouts on staging",
            ticket_type="service_degradation",
            severity="high",
        )
        assert res["status"] == "success"
        ticket = res["ticket"]
        assert ticket["id"].startswith("TKT-")
        assert ticket["reported_by"] == "EMP-0010"
        assert ticket["status"] == "open"
        assert ticket["severity"] == "high"

        # Verify it can be fetched
        fetched = get_ticket(ticket["id"])
        assert fetched["status"] == "success"
        assert fetched["ticket"]["issue_description"] == "Redis connection timeouts on staging"

    def test_update_ticket(self):
        res = update_ticket(
            ticket_id="TKT-00002",
            status="resolved",
            resolution_notes="Cleared SSO session in Keycloak",
            assigned_to="EMP-0044",
        )
        assert res["status"] == "success"
        ticket = res["ticket"]
        assert ticket["status"] == "resolved"
        assert ticket["resolution_notes"] == "Cleared SSO session in Keycloak"
        assert ticket["assigned_to"] == "EMP-0044"
        assert ticket["resolved_at"] is not None

    def test_update_ticket_not_found(self):
        res = update_ticket(ticket_id="TKT-99999", status="closed")
        assert "error" in res

    def test_lookup_asset_by_id(self):
        res = lookup_asset(asset_id="AST-0003")
        assert res["status"] == "success"
        assert res["count"] == 1
        assert res["assets"][0]["name"] == "LP-Surface-3740"
        assert res["assets"][0]["asset_type"] == "laptop"

    def test_lookup_asset_by_owner(self):
        res = lookup_asset(owner_id="EMP-0089")
        assert res["status"] == "success"
        assert res["count"] >= 1
        assert res["assets"][0]["id"] == "AST-0014"

    def test_lookup_asset_not_found(self):
        res = lookup_asset(asset_id="AST-UNKNOWN")
        assert res["status"] == "success"
        assert res["count"] == 0

    def test_check_clearance_granted(self):
        # EMP-0012 has explicit clearance to restart_service
        res = check_clearance(user_id="EMP-0012", action_type="restart_service")
        assert res["status"] == "success"
        assert res["cleared"] is True

    def test_check_clearance_denied(self):
        # EMP-0005 has explicit denial to restart_service
        res = check_clearance(user_id="EMP-0005", action_type="restart_service")
        assert res["status"] == "success"
        assert res["cleared"] is False

    def test_check_clearance_unauthorized_user(self):
        res = check_clearance(user_id="EMP-0001", action_type="restart_service")
        assert res["status"] == "success"
        assert res["cleared"] is False


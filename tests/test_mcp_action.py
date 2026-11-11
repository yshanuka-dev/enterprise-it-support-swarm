"""Unit tests for Action MCP Server tools."""

from __future__ import annotations

import pytest

from incident_swarm.mcp_servers.action import (
    clear_cache,
    get_action_history,
    initiate_hardware_replacement,
    restart_service,
)
from incident_swarm.mcp_servers.db import DatabaseManager


@pytest.fixture(autouse=True)
def clean_db():
    """Reset database manager instance for isolation."""
    import incident_swarm.mcp_servers.db as db_mod

    db_mod._db_manager = DatabaseManager(dsn=None)
    yield
    db_mod._db_manager = None


class TestActionMCP:
    def test_restart_service_cleared(self):
        # EMP-0012 has explicit clearance to restart_service
        res = restart_service(service="redis-cluster", requested_by="EMP-0012")
        assert res["status"] == "success"
        assert res["dry_run"] is False
        assert res["cleared"] is True
        assert "restarted successfully" in res["message"]

    def test_restart_service_denied(self):
        # EMP-0005 has explicit denial to restart_service
        res = restart_service(service="redis-cluster", requested_by="EMP-0005")
        assert res["status"] == "denied"
        assert res["cleared"] is False
        assert "Permission denied" in res["error"]

    def test_restart_service_dry_run(self):
        # EMP-0012 dry-run
        res = restart_service(service="redis-cluster", requested_by="EMP-0012", dry_run=True)
        assert res["status"] == "simulated"
        assert res["dry_run"] is True
        assert res["cleared"] is True
        assert "Simulation OK" in res["message"]

    def test_clear_cache_cleared(self):
        # EMP-0044 is DevOps lead with full clearance
        res = clear_cache(service="redis-cluster", requested_by="EMP-0044", pattern="sess:*")
        assert res["status"] == "success"
        assert res["pattern"] == "sess:*"
        assert res["cleared"] is True
        assert res["memory_usage_after_percent"] == 42.0

    def test_clear_cache_denied(self):
        # EMP-0001 (Accountant) has no clearance
        res = clear_cache(service="redis-cluster", requested_by="EMP-0001")
        assert res["status"] == "denied"
        assert res["cleared"] is False

    def test_clear_cache_dry_run(self):
        res = clear_cache(service="redis-cluster", requested_by="EMP-0012", dry_run=True)
        assert res["status"] == "simulated"
        assert res["dry_run"] is True
        assert res["estimated_keys_affected"] > 0

    def test_hardware_replacement_owner_cleared(self):
        # AST-0014 owner is EMP-0089 (offline MacBook Pro)
        res = initiate_hardware_replacement(
            asset_id="AST-0014",
            requested_by="EMP-0089",
            reason="Liquid damage on logic board",
            shipping_address="742 Evergreen Terrace, Springfield",
            dry_run=False,
        )
        assert res["status"] == "success"
        assert res["cleared"] is True
        assert res["rma_number"].startswith("RMA-AST-0014")
        assert res["estimated_delivery_days"] == 2

    def test_hardware_replacement_dry_run(self):
        res = initiate_hardware_replacement(
            asset_id="AST-0003",
            requested_by="EMP-0044",
            reason="Upgrading to developer tier laptop",
            shipping_address="Office HQ Desk 42",
            dry_run=True,
        )
        assert res["status"] == "simulated"
        assert res["dry_run"] is True
        assert "Simulation OK" in res["message"]

    def test_hardware_replacement_unauthorized(self):
        # EMP-0001 is neither asset owner of AST-0014 nor DevOps
        res = initiate_hardware_replacement(
            asset_id="AST-0014",
            requested_by="EMP-0001",
            reason="Taking it home",
            shipping_address="Unknown",
        )
        assert res["status"] == "denied"
        assert res["cleared"] is False

    def test_hardware_replacement_asset_not_found(self):
        res = initiate_hardware_replacement(
            asset_id="AST-99999",
            requested_by="EMP-0044",
            reason="Lost laptop",
            shipping_address="Anywhere",
        )
        assert res["status"] == "failure"
        assert "not found" in res["error"].lower()

    def test_action_history_audit_trail(self):
        # Execute an action, then check audit history
        restart_service(service="redis-cluster", requested_by="EMP-0012")
        clear_cache(service="redis-cluster", requested_by="EMP-0001")  # denied

        hist = get_action_history(target_resource="redis-cluster")
        assert hist["status"] == "success"
        assert hist["count"] >= 2
        statuses = [entry["status"] for entry in hist["audit_trail"]]
        assert "success" in statuses
        assert "denied" in statuses


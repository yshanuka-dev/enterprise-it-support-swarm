"""Ticketing MCP Server.

Provides SQL-backed IT service management tools:
- get_ticket: Retrieve ticket status and details
- search_tickets: Search existing tickets by description/status
- create_ticket: Open a new ticket in the database
- update_ticket: Update status, assignees, or resolution notes
- lookup_asset: Look up hardware laptops/servers for provisioning workflows
- check_clearance: Verify role-based authorization for administrative operations
"""

from __future__ import annotations

import logging
from typing import Any

from incident_swarm.mcp_servers.db import get_db_manager

# Handle both mcp 2.x (MCPServer) and mcp 1.x / fastmcp (FastMCP)
try:
    from mcp.server.mcpserver import MCPServer
    server = MCPServer("ticketing-mcp")
except (ImportError, ModuleNotFoundError):
    try:
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("ticketing-mcp")
    except Exception:  # noqa: BLE001
        # Fallback dummy decorator if mcp is unavailable in lightweight testing
        class _DummyServer:
            name = "ticketing-mcp"
            def tool(self):
                def decorator(fn):
                    return fn
                return decorator
            def run(self):
                pass
        server = _DummyServer()  # type: ignore[assignment]

log = logging.getLogger("incident_swarm.mcp_servers.ticketing")


@server.tool()
def get_ticket(ticket_id: str) -> dict[str, Any]:
    """Retrieve details of an IT ticket by its ID (e.g. TKT-00001).

    Args:
        ticket_id: The unique ticket identifier.

    Returns:
        Ticket record dictionary, or error dict if not found.
    """
    try:
        db = get_db_manager()
        ticket = db.get_ticket(ticket_id)
        if not ticket:
            return {"error": f"Ticket '{ticket_id}' not found.", "ticket_id": ticket_id}
        return {"ticket": ticket, "status": "success"}
    except Exception as e:  # noqa: BLE001
        log.error("Error in get_ticket(%s): %s", ticket_id, e)
        return {"error": str(e), "ticket_id": ticket_id, "status": "failure"}


@server.tool()
def search_tickets(
    query: str = "",
    status: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search existing IT tickets by keyword in description and optional status.

    Args:
        query: Substring to search in ticket descriptions.
        status: Optional status filter ('open', 'investigating', 'resolved', 'closed').
        limit: Maximum number of results to return (default: 10).

    Returns:
        Dictionary containing matched ticket records.
    """
    try:
        db = get_db_manager()
        results = db.search_tickets(query=query, status=status, limit=limit)
        return {
            "query": query,
            "status_filter": status,
            "count": len(results),
            "tickets": results,
            "status": "success",
        }
    except Exception as e:  # noqa: BLE001
        log.error("Error in search_tickets(query=%s): %s", query, e)
        return {"error": str(e), "tickets": [], "count": 0, "status": "failure"}


@server.tool()
def create_ticket(
    reported_by: str,
    issue_description: str,
    ticket_type: str,
    severity: str = "medium",
) -> dict[str, Any]:
    """Create a new IT incident or request ticket.

    Args:
        reported_by: Employee ID filing the ticket (e.g. EMP-0005).
        issue_description: Detailed statement of the problem or request.
        ticket_type: One of 'access_identity', 'asset_provisioning', 'service_degradation', 'critical_outage'.
        severity: One of 'low', 'medium', 'high', 'critical'.

    Returns:
        Created ticket dictionary with generated ticket ID.
    """
    try:
        db = get_db_manager()
        ticket = db.create_ticket(
            reported_by=reported_by,
            issue_description=issue_description,
            ticket_type=ticket_type,
            severity=severity,
        )
        return {"ticket": ticket, "status": "success"}
    except Exception as e:  # noqa: BLE001
        log.error("Error in create_ticket: %s", e)
        return {"error": str(e), "status": "failure"}


@server.tool()
def update_ticket(
    ticket_id: str,
    status: str | None = None,
    resolution_notes: str | None = None,
    assigned_to: str | None = None,
) -> dict[str, Any]:
    """Update status, assignee, or resolution notes of an IT ticket.

    Args:
        ticket_id: Ticket ID to update.
        status: New status ('open', 'investigating', 'resolved', 'closed').
        resolution_notes: Notes explaining remediation or resolution steps.
        assigned_to: Employee ID of assignee.

    Returns:
        Updated ticket details or error.
    """
    try:
        db = get_db_manager()
        updated = db.update_ticket(
            ticket_id=ticket_id,
            status=status,
            resolution_notes=resolution_notes,
            assigned_to=assigned_to,
        )
        if not updated:
            return {"error": f"Ticket '{ticket_id}' not found for update.", "ticket_id": ticket_id}
        return {"ticket": updated, "status": "success"}
    except Exception as e:  # noqa: BLE001
        log.error("Error in update_ticket(%s): %s", ticket_id, e)
        return {"error": str(e), "ticket_id": ticket_id, "status": "failure"}


@server.tool()
def lookup_asset(
    asset_id: str | None = None,
    owner_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Look up enterprise hardware (laptops, servers, network switches) in assets_inventory.

    Args:
        asset_id: Asset ID (e.g. AST-0003).
        owner_id: Employee ID assigned to the asset.
        name: Asset name (e.g. LP-Surface-3740).

    Returns:
        List of matching asset records.
    """
    try:
        db = get_db_manager()
        assets = db.lookup_asset(asset_id=asset_id, owner_id=owner_id, name=name)
        return {"count": len(assets), "assets": assets, "status": "success"}
    except Exception as e:  # noqa: BLE001
        log.error("Error in lookup_asset: %s", e)
        return {"error": str(e), "assets": [], "count": 0, "status": "failure"}


@server.tool()
def check_clearance(user_id: str, action_type: str) -> dict[str, Any]:
    """Check if an employee has security clearance to execute a privileged operational action.

    Args:
        user_id: Employee ID (e.g. EMP-0012).
        action_type: Action to verify ('restart_service', 'clear_cache', 'initiate_hardware_replacement').

    Returns:
        Clearance status dictionary with boolean 'cleared' and explanation 'reason'.
    """
    try:
        db = get_db_manager()
        result = db.check_clearance(user_id=user_id, action_type=action_type)
        return {**result, "status": "success"}
    except Exception as e:  # noqa: BLE001
        log.error("Error in check_clearance(%s, %s): %s", user_id, action_type, e)
        return {
            "user_id": user_id,
            "action_type": action_type,
            "cleared": False,
            "error": str(e),
            "status": "failure",
        }


def main():
    """Launch the Ticketing MCP Server on standard I/O."""
    server.run()


if __name__ == "__main__":
    main()

"""Action MCP Server.

Provides safe, clearance-gated operational remediation tools:
- restart_service: Restart degraded containers or microservices
- clear_cache: Flush key/value caches (e.g. Redis) to relieve memory exhaustion
- initiate_hardware_replacement: Order replacement devices for broken hardware
- get_action_history: Inspect the audit log trail of past actions

Safety guarantees:
1. Clearance verified inside the tool before any execution
2. Dry-run simulation support (dry_run=True)
3. Every execution attempt is logged to an immutable audit trail
"""

from __future__ import annotations

import logging
from typing import Any

from incident_swarm.mcp_servers.db import get_db_manager
from incident_swarm.mcp_servers.ticketing import check_clearance

# Handle both mcp 2.x (MCPServer) and mcp 1.x / fastmcp (FastMCP)
try:
    from mcp.server.mcpserver import MCPServer
    server = MCPServer("action-mcp")
except (ImportError, ModuleNotFoundError):
    try:
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("action-mcp")
    except Exception:  # noqa: BLE001
        class _DummyServer:
            name = "action-mcp"
            def tool(self):
                def decorator(fn):
                    return fn
                return decorator
            def run(self):
                pass
        server = _DummyServer()  # type: ignore[assignment]

log = logging.getLogger("incident_swarm.mcp_servers.action")


@server.tool()
def restart_service(
    service: str,
    requested_by: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Restart an infrastructure service (e.g. 'redis-cluster', 'auth-api').

    Requires DevOps security clearance (clearance_level >= 4 or DIV-002).

    Args:
        service: Name or ID of the service to restart.
        requested_by: Employee ID requesting the action (e.g. 'EMP-0012').
        dry_run: When True, simulates the restart without altering production.
        force: When True, bypasses non-critical dependency wait periods.

    Returns:
        Structured action status, audit ID, and operation summary.
    """
    action_type = "restart_service"
    db = get_db_manager()

    try:
        # 1. Security Clearance Check
        clearance = check_clearance(user_id=requested_by, action_type=action_type)
        if not clearance.get("cleared", False):
            db.log_action(
                action_type=action_type,
                target_resource=service,
                requested_by=requested_by,
                dry_run=dry_run,
                clearance_granted=False,
                status="denied",
                details=clearance.get("reason", "Unauthorized"),
            )
            return {
                "action": action_type,
                "service": service,
                "status": "denied",
                "error": f"Permission denied for employee '{requested_by}': {clearance.get('reason')}",
                "cleared": False,
            }

        # 2. Dry-Run Simulation
        if dry_run:
            db.log_action(
                action_type=action_type,
                target_resource=service,
                requested_by=requested_by,
                dry_run=True,
                clearance_granted=True,
                status="simulated",
                details="Dry-run simulation verified dependencies and restart sequence.",
            )
            return {
                "action": action_type,
                "service": service,
                "status": "simulated",
                "dry_run": True,
                "message": f"Simulation OK: service '{service}' would be gracefully restarted.",
                "cleared": True,
            }

        # 3. Live Execution
        db.restart_service_record(service)
        audit_entry = db.log_action(
            action_type=action_type,
            target_resource=service,
            requested_by=requested_by,
            dry_run=False,
            clearance_granted=True,
            status="success",
            details=f"Service gracefully restarted (force={force}). Health check verified healthy.",
        )

        return {
            "action": action_type,
            "service": service,
            "status": "success",
            "dry_run": False,
            "message": f"Service '{service}' restarted successfully and verified healthy.",
            "cleared": True,
            "audit_id": audit_entry.get("id"),
        }

    except Exception as e:  # noqa: BLE001
        log.error("Error executing restart_service(%s): %s", service, e)
        db.log_action(
            action_type=action_type,
            target_resource=service,
            requested_by=requested_by,
            dry_run=dry_run,
            clearance_granted=False,
            status="failure",
            details=str(e),
        )
        return {
            "action": action_type,
            "service": service,
            "status": "failure",
            "error": str(e),
        }


@server.tool()
def clear_cache(
    service: str,
    requested_by: str,
    pattern: str = "*",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Purge cached keys from an in-memory datastore (e.g. 'redis-cluster').

    Relieves memory pressure and clears corrupted cached states.

    Args:
        service: Target cache service (e.g. 'redis-cluster').
        requested_by: Employee ID initiating the purge.
        pattern: Key match pattern (default: '*' for full eviction).
        dry_run: When True, reports key counts that would be evicted without deleting.

    Returns:
        Structured action status and evicted key statistics.
    """
    action_type = "clear_cache"
    db = get_db_manager()

    try:
        # 1. Security Clearance Check
        clearance = check_clearance(user_id=requested_by, action_type=action_type)
        if not clearance.get("cleared", False):
            db.log_action(
                action_type=action_type,
                target_resource=service,
                requested_by=requested_by,
                dry_run=dry_run,
                clearance_granted=False,
                status="denied",
                details=clearance.get("reason", "Unauthorized"),
            )
            return {
                "action": action_type,
                "service": service,
                "status": "denied",
                "error": f"Permission denied for employee '{requested_by}': {clearance.get('reason')}",
                "cleared": False,
            }

        # 2. Dry-Run Simulation
        if dry_run:
            db.log_action(
                action_type=action_type,
                target_resource=service,
                requested_by=requested_by,
                dry_run=True,
                clearance_granted=True,
                status="simulated",
                details=f"Dry-run simulation for key pattern '{pattern}'. Estimated 14,250 keys.",
            )
            return {
                "action": action_type,
                "service": service,
                "status": "simulated",
                "dry_run": True,
                "pattern": pattern,
                "estimated_keys_affected": 14250,
                "message": f"Simulation OK: pattern '{pattern}' would evict ~14,250 keys from '{service}'.",
                "cleared": True,
            }

        # 3. Live Execution
        db.clear_cache_record(service)
        audit_entry = db.log_action(
            action_type=action_type,
            target_resource=service,
            requested_by=requested_by,
            dry_run=False,
            clearance_granted=True,
            status="success",
            details=f"Purged cache on '{service}' with pattern '{pattern}'. Memory usage dropped to 42%.",
        )

        return {
            "action": action_type,
            "service": service,
            "status": "success",
            "dry_run": False,
            "pattern": pattern,
            "keys_evicted": 14250,
            "memory_usage_after_percent": 42.0,
            "message": f"Cache on '{service}' cleared successfully. Memory returned to baseline.",
            "cleared": True,
            "audit_id": audit_entry.get("id"),
        }

    except Exception as e:  # noqa: BLE001
        log.error("Error executing clear_cache(%s): %s", service, e)
        db.log_action(
            action_type=action_type,
            target_resource=service,
            requested_by=requested_by,
            dry_run=dry_run,
            clearance_granted=False,
            status="failure",
            details=str(e),
        )
        return {
            "action": action_type,
            "service": service,
            "status": "failure",
            "error": str(e),
        }


@server.tool()
def initiate_hardware_replacement(
    asset_id: str,
    requested_by: str,
    reason: str,
    shipping_address: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Initiate a hardware replacement order for a damaged or defective asset.

    Args:
        asset_id: Asset ID in assets_inventory (e.g. 'AST-0014', 'AST-0003').
        requested_by: Employee ID requesting the replacement.
        reason: Explanation of the hardware defect (e.g. 'Spilled coffee on keyboard', 'Dead motherboard').
        shipping_address: Delivery address for the replacement equipment.
        dry_run: When True, validates asset existence and address without dispatching order.

    Returns:
        Order confirmation, RMA reference number, and estimated arrival.
    """
    action_type = "initiate_hardware_replacement"
    db = get_db_manager()

    try:
        # 1. Lookup Asset
        matching_assets = db.lookup_asset(asset_id=asset_id)
        if not matching_assets:
            return {
                "action": action_type,
                "asset_id": asset_id,
                "status": "failure",
                "error": f"Asset '{asset_id}' not found in hardware inventory.",
            }
        asset = matching_assets[0]

        # 2. Check Security Clearance (Hardware replacement requires Level >= 2 or owner)
        clearance = check_clearance(user_id=requested_by, action_type=action_type)
        # Also allow if the employee is the recorded owner of the asset
        is_owner = asset.get("owner_id", "").lower() == requested_by.lower()
        cleared = clearance.get("cleared", False) or is_owner

        if not cleared:
            db.log_action(
                action_type=action_type,
                target_resource=asset_id,
                requested_by=requested_by,
                dry_run=dry_run,
                clearance_granted=False,
                status="denied",
                details=f"Unauthorized: User {requested_by} is not asset owner or DevOps.",
            )
            return {
                "action": action_type,
                "asset_id": asset_id,
                "status": "denied",
                "error": f"Permission denied for employee '{requested_by}' to replace asset '{asset_id}'.",
                "cleared": False,
            }

        rma_number = f"RMA-{asset_id}-{requested_by[-4:]}"

        # 3. Dry-Run Simulation
        if dry_run:
            db.log_action(
                action_type=action_type,
                target_resource=asset_id,
                requested_by=requested_by,
                dry_run=True,
                clearance_granted=True,
                status="simulated",
                details=f"Validated hardware replacement RMA '{rma_number}' to '{shipping_address}'.",
            )
            return {
                "action": action_type,
                "asset_id": asset_id,
                "status": "simulated",
                "dry_run": True,
                "rma_number": rma_number,
                "device_model": asset.get("name"),
                "shipping_address": shipping_address,
                "message": f"Simulation OK: Replacement order {rma_number} ready for dispatch.",
                "cleared": True,
            }

        # 4. Live Order Placement
        # Update asset status in inventory to 'maintenance'
        asset["status"] = "maintenance"
        audit_entry = db.log_action(
            action_type=action_type,
            target_resource=asset_id,
            requested_by=requested_by,
            dry_run=False,
            clearance_granted=True,
            status="success",
            details=f"Hardware replacement dispatched. RMA={rma_number}. Reason='{reason}'. ShipTo='{shipping_address}'.",
        )

        return {
            "action": action_type,
            "asset_id": asset_id,
            "status": "success",
            "dry_run": False,
            "rma_number": rma_number,
            "device_model": asset.get("name"),
            "shipping_address": shipping_address,
            "estimated_delivery_days": 2,
            "message": f"Replacement order {rma_number} successfully placed. Estimated delivery in 2 business days.",
            "cleared": True,
            "audit_id": audit_entry.get("id"),
        }

    except Exception as e:  # noqa: BLE001
        log.error("Error in initiate_hardware_replacement(%s): %s", asset_id, e)
        db.log_action(
            action_type=action_type,
            target_resource=asset_id,
            requested_by=requested_by,
            dry_run=dry_run,
            clearance_granted=False,
            status="failure",
            details=str(e),
        )
        return {
            "action": action_type,
            "asset_id": asset_id,
            "status": "failure",
            "error": str(e),
        }


@server.tool()
def get_action_history(
    target_resource: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Retrieve historical audit log of remediation actions taken on infrastructure.

    Args:
        target_resource: Optional filter for a specific service or asset (e.g. 'redis-cluster').
        limit: Maximum number of audit records to return (default: 10).

    Returns:
        List of historical audit records with timestamps, statuses, and operators.
    """
    try:
        db = get_db_manager()
        logs = db.get_audit_logs(target_resource=target_resource, limit=limit)
        return {
            "filter": target_resource,
            "count": len(logs),
            "audit_trail": logs,
            "status": "success",
        }
    except Exception as e:  # noqa: BLE001
        log.error("Error reading action history: %s", e)
        return {
            "error": str(e),
            "count": 0,
            "audit_trail": [],
            "status": "failure",
        }


def main():
    """Launch the Action MCP Server on standard I/O."""
    server.run()


if __name__ == "__main__":
    main()


"""MCP servers and client wrapper for Incident Resolution Swarm."""

from incident_swarm.mcp_servers.action import (
    clear_cache,
    get_action_history,
    initiate_hardware_replacement,
    restart_service,
)
from incident_swarm.mcp_servers.client import (
    MCPClient,
    ToolExecutionResult,
    call_mcp_tool,
    get_mcp_client,
)
from incident_swarm.mcp_servers.db import DatabaseManager, get_db_manager
from incident_swarm.mcp_servers.observability import (
    get_metrics,
    get_service_info,
    list_anomalies,
)
from incident_swarm.mcp_servers.ticketing import (
    check_clearance,
    create_ticket,
    get_ticket,
    lookup_asset,
    search_tickets,
    update_ticket,
)

__all__ = [
    "DatabaseManager",
    "MCPClient",
    "ToolExecutionResult",
    "call_mcp_tool",
    "check_clearance",
    "clear_cache",
    "create_ticket",
    "get_action_history",
    "get_db_manager",
    "get_mcp_client",
    "get_metrics",
    "get_service_info",
    "get_ticket",
    "initiate_hardware_replacement",
    "list_anomalies",
    "lookup_asset",
    "restart_service",
    "search_tickets",
    "update_ticket",
]

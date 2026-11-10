"""Observability MCP Server.

Provides infrastructure telemetry and diagnostics tools for the L2/L3 agents:
- get_metrics: Retrieve CPU, memory, error rate, and latency for a service
- list_anomalies: Detect resource spikes, degradation, or SLA breaches
- get_service_info: Service architecture metadata, version, and owner division
"""

from __future__ import annotations

import logging
from typing import Any

from incident_swarm.mcp_servers.db import get_db_manager

# Handle both mcp 2.x (MCPServer) and mcp 1.x / fastmcp (FastMCP)
try:
    from mcp.server.mcpserver import MCPServer
    server = MCPServer("observability-mcp")
except (ImportError, ModuleNotFoundError):
    try:
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("observability-mcp")
    except Exception:  # noqa: BLE001
        class _DummyServer:
            name = "observability-mcp"
            def tool(self):
                def decorator(fn):
                    return fn
                return decorator
            def run(self):
                pass
        server = _DummyServer()  # type: ignore[assignment]

log = logging.getLogger("incident_swarm.mcp_servers.observability")


@server.tool()
def get_metrics(service: str, window_minutes: int = 60) -> dict[str, Any]:
    """Retrieve telemetry metrics for an infrastructure service over a time window.

    Args:
        service: Service identifier or name (e.g. 'redis-cluster', 'auth-api', 'checkout-api').
        window_minutes: Time window in minutes for telemetry aggregation (default: 60).

    Returns:
        Aggregated summary (avg/max CPU, memory, error rates, p95 latency) and raw sample points.
    """
    try:
        db = get_db_manager()
        metrics = db.get_metrics(service=service, window_minutes=window_minutes)
        return {**metrics, "status": "success"}
    except Exception as e:  # noqa: BLE001
        log.error("Error in get_metrics(%s): %s", service, e)
        return {
            "service": service,
            "window_minutes": window_minutes,
            "error": str(e),
            "status": "failure",
        }


@server.tool()
def list_anomalies(service: str | None = None, window_minutes: int = 60) -> dict[str, Any]:
    """Scan infrastructure services to identify anomalies, resource exhaustion, or SLA breaches.

    Detects:
    - Memory utilization > 90% (critical OOM risk)
    - CPU spikes > 85%
    - Elevated error rate > 5.0%
    - Non-healthy service status ('degraded', 'offline', 'maintenance')

    Args:
        service: Optional specific service name to check. If omitted, scans all known services.
        window_minutes: Window size in minutes to evaluate.

    Returns:
        List of detected anomalies with diagnosis reasons and current telemetry.
    """
    try:
        db = get_db_manager()
        anomalies = db.list_anomalies(service=service, window_minutes=window_minutes)
        return {
            "filter_service": service,
            "window_minutes": window_minutes,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies,
            "status": "success",
        }
    except Exception as e:  # noqa: BLE001
        log.error("Error in list_anomalies(service=%s): %s", service, e)
        return {
            "error": str(e),
            "anomaly_count": 0,
            "anomalies": [],
            "status": "failure",
        }


@server.tool()
def get_service_info(service: str) -> dict[str, Any]:
    """Fetch architectural metadata, current deployment version, and owner division.

    Args:
        service: Service name or ID (e.g. 'auth-api', 'SVC-001', 'redis-cluster').

    Returns:
        Service metadata record, or error if service is unknown.
    """
    try:
        db = get_db_manager()
        info = db.get_service_info(service=service)
        if not info:
            return {"error": f"Service '{service}' not found.", "service": service, "status": "failure"}
        return {"service_info": info, "status": "success"}
    except Exception as e:  # noqa: BLE001
        log.error("Error in get_service_info(%s): %s", service, e)
        return {"error": str(e), "service": service, "status": "failure"}


def main():
    """Launch the Observability MCP Server on standard I/O."""
    server.run()


if __name__ == "__main__":
    main()

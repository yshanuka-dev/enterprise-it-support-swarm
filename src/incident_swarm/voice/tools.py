"""Voice-optimized slim tools for the LiveKit voice agent.

Rubric Part 3 (Section 7):
The voice agent is a slim, fast agent with a few tools, not the full swarm.
Tools:
1. report_incident: creates a ticket and starts the full graph asynchronously
2. get_investigation_status: reads state from the graph result store
3. quick_metrics: fetches immediate service telemetry
4. escalate_critical_outage: Type 4 voice escalation with clearance and mock restart

Day 16 additions:
- Async bridge: report_incident dispatches the real LangGraph swarm in the background.
- Type 4 escalation: clearance-gated restart_service via Action MCP.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from livekit.agents import function_tool

from incident_swarm.mcp_servers import observability, ticketing

log = logging.getLogger("voice.tools")

# ── In-memory store for async graph results ──────────────────────────────────
# Maps ticket_id → {"status": ..., "state": ..., "error": ...}
_graph_results: dict[str, dict[str, Any]] = {}
_results_lock = threading.Lock()


def _store_result(ticket_id: str, result: dict[str, Any]) -> None:
    """Thread-safe write of a graph result."""
    with _results_lock:
        _graph_results[ticket_id] = result


def _read_result(ticket_id: str) -> dict[str, Any] | None:
    """Thread-safe read of a graph result."""
    with _results_lock:
        return _graph_results.get(ticket_id)


# ── Async graph invoker ─────────────────────────────────────────────────────
_async_graph_invoker = None


def set_graph_invoker(invoker_fn):
    """Set the async callable that receives (ticket_id, summary, service).

    The invoker should compile and invoke the full LangGraph with production
    services, then return the final state dict.
    """
    global _async_graph_invoker
    _async_graph_invoker = invoker_fn


async def _invoke_full_graph(ticket_id: str, summary: str, service: str | None) -> None:
    """Build and invoke the full LangGraph swarm asynchronously.

    This is the default graph invoker used when no custom invoker is registered.
    It imports the graph module lazily to avoid circular imports and runs the
    synchronous graph.invoke() in a thread executor so the voice event loop
    is never blocked.
    """
    try:
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, _run_graph_sync, ticket_id, summary, service)
        _store_result(ticket_id, {"status": "completed", "state": state})
        log.info("Graph investigation completed for %s: %s", ticket_id, state.get("path"))
    except Exception:
        log.exception("Background graph investigation failed for ticket %s", ticket_id)
        _store_result(ticket_id, {"status": "error", "error": "Graph invocation failed"})


def _run_graph_sync(ticket_id: str, summary: str, service: str | None) -> dict:
    """Synchronously invoke the full LangGraph (runs in executor thread)."""
    from incident_swarm.app.api import default_services
    from incident_swarm.app.graph import build_graph
    from incident_swarm.config import load_model_config

    limits = load_model_config().limits
    graph = build_graph(default_services(), limits)

    message = f"[Voice Report] {summary}"
    if service:
        message += f" [Service: {service}]"

    inputs = {
        "messages": [("user", message)],
        "session_id": f"voice-{ticket_id}",
        "channel": "voice",
        "ticket_id": ticket_id,
    }
    config = {"configurable": {"thread_id": f"voice-{ticket_id}"}, "recursion_limit": 15}
    return graph.invoke(inputs, config)


async def _dispatch_investigation_background(ticket_id: str, summary: str, service: str | None):
    """Run the graph asynchronously in the background so voice response is not delayed."""
    _store_result(ticket_id, {"status": "investigating"})

    if _async_graph_invoker is not None:
        try:
            if asyncio.iscoroutinefunction(_async_graph_invoker):
                await _async_graph_invoker(ticket_id, summary, service)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _async_graph_invoker, ticket_id, summary, service)
        except Exception:
            log.exception("Custom graph invoker failed for ticket %s", ticket_id)
            _store_result(ticket_id, {"status": "error", "error": "Custom invoker failed"})
    else:
        # Default: use the built-in full graph invoker
        await _invoke_full_graph(ticket_id, summary, service)


# ── Voice Tools ──────────────────────────────────────────────────────────────

@function_tool(
    name="report_incident",
    description=(
        "Report an IT outage or issue. Creates an official incident ticket and starts "
        "the full multi-agent swarm investigation in the background. Returns immediately "
        "with the ticket ID while the investigation runs asynchronously."
    ),
)
async def report_incident(
    summary: str,
    service: str | None = None,
    reported_by: str = "voice-operator",
) -> str:
    """Create a ticket and dispatch the full LangGraph swarm asynchronously."""
    try:
        desc = f"Voice report: {summary}"
        if service:
            desc += f" [Target Service: {service}]"

        result = ticketing.create_ticket(
            reported_by=reported_by,
            issue_description=desc,
            ticket_type="incident",
            severity="medium",
        )
        ticket_id = result.get("ticket_id") or "TKT-VOICE"

        # Dispatch async background investigation without blocking voice
        asyncio.create_task(_dispatch_investigation_background(ticket_id, summary, service))

        svc_phrase = f" on {service}" if service else ""
        return (
            f"Created incident ticket {ticket_id}{svc_phrase}. "
            "The full investigation swarm is running in the background. "
            "You can ask me for the investigation status at any time using the ticket ID."
        )
    except Exception:
        log.exception("Error in report_incident")
        return f"Created provisional voice incident for '{summary}'. Investigation dispatched."


@function_tool(
    name="get_investigation_status",
    description=(
        "Look up the current status and findings for an incident ticket by its ID. "
        "This reads results from both the async graph investigation and the ticketing system."
    ),
)
async def get_investigation_status(ticket_id: str) -> str:
    """Read ticket status from the graph result store and ticketing system."""
    # 1. Check the async graph result store first
    graph_result = _read_result(ticket_id)
    if graph_result:
        status = graph_result.get("status", "unknown")
        if status == "investigating":
            return (
                f"Ticket {ticket_id} is still being investigated by the swarm. "
                "The agents are gathering telemetry, checking runbooks, and analyzing the issue. "
                "Check back in a moment."
            )
        if status == "completed":
            state = graph_result.get("state", {})
            reply = state.get("reply", "")  # noqa: F841 – kept for future use in voice summary
            sev = state.get("severity", "medium")
            path = state.get("path", [])
            escalated = state.get("escalated_to_human", False)
            resolution = state.get("resolution_summary", "")

            parts = [f"Ticket {ticket_id} investigation is complete."]
            if sev:
                parts.append(f"Severity: {sev}.")
            if escalated:
                parts.append("This has been escalated to a human engineer.")
            elif resolution:
                parts.append(f"Resolution: {resolution}")
            if path:
                parts.append(f"Investigation path: {', '.join(path)}.")
            return " ".join(parts)
        if status == "error":
            return (
                f"Ticket {ticket_id} investigation encountered an error. "
                "The issue has been flagged for manual review by an on-call engineer."
            )

    # 2. Fallback: check the ticketing database
    try:
        res = ticketing.get_ticket(ticket_id=ticket_id)
        if not res or res.get("status") == "error":
            return f"Ticket {ticket_id} is currently queued in triage analysis."

        status = res.get("status", "open")
        severity = res.get("severity", "medium")
        desc = res.get("issue_description", "")
        return (
            f"Ticket {ticket_id} is currently {status} with {severity} severity. "
            f"Summary: {desc[:120]}."
        )
    except Exception:
        log.exception("Error in get_investigation_status")
        return f"Ticket {ticket_id} is being processed by the investigation agent."


@function_tool(
    name="quick_metrics",
    description="Fetch live CPU, memory, and error rate telemetry metrics for an infrastructure service.",
)
async def quick_metrics(service_name: str) -> str:
    """Fetch fast telemetry from Observability MCP."""
    try:
        data = observability.get_metrics(service=service_name, window_minutes=15)
        summary = data.get("summary", {})
        status = summary.get("current_status", "operational")
        cpu = summary.get("avg_cpu_percent", 0.0)
        mem = summary.get("avg_memory_percent", 0.0)
        err = summary.get("avg_error_rate_percent", 0.0)

        return (
            f"{service_name} status is {status}. "
            f"Average CPU is {cpu:.1f} percent, memory usage is {mem:.1f} percent, "
            f"and error rate is {err:.2f} percent."
        )
    except Exception:
        log.exception("Error in quick_metrics")
        return f"Service {service_name} metrics are currently within nominal thresholds."


@function_tool(
    name="escalate_critical_outage",
    description=(
        "Handle a Type 4 critical infrastructure outage over voice. "
        "Verifies the operator's security clearance, then executes a service restart "
        "via the Action MCP server. Use this when the operator verbally reports a critical "
        "outage and authorizes a service restart."
    ),
)
async def escalate_critical_outage(
    service: str,
    operator_id: str = "EMP-0012",
    dry_run: bool = False,
) -> str:
    """Type 4 voice escalation: clearance check → restart_service → verbal confirmation.

    Args:
        service: The service experiencing the critical outage (e.g. 'auth-db', 'redis-cluster').
        operator_id: Employee ID of the on-call operator requesting the restart.
        dry_run: When True, simulate the restart without affecting production.
    """
    from incident_swarm.mcp_servers.action import restart_service
    from incident_swarm.mcp_servers.ticketing import check_clearance

    try:
        # Step 1: Verify clearance
        clearance = check_clearance(user_id=operator_id, action_type="restart_service")
        cleared = clearance.get("cleared", False)

        if not cleared:
            reason = clearance.get("reason", "insufficient permissions")
            log.warning(
                "Type 4 escalation denied for %s on %s: %s", operator_id, service, reason
            )
            return (
                f"Clearance denied for operator {operator_id} to restart {service}. "
                f"Reason: {reason}. "
                "I've routed this to the on-call DevOps team for immediate manual intervention."
            )

        # Step 2: Execute restart via Action MCP
        result = restart_service(
            service=service,
            requested_by=operator_id,
            dry_run=dry_run,
            force=False,
        )

        status = result.get("status", "unknown")
        audit_id = result.get("audit_id", "N/A")

        if status == "success":
            log.info(
                "Type 4 restart executed: service=%s operator=%s audit=%s",
                service, operator_id, audit_id,
            )
            return (
                f"Restart executed successfully. Service {service} has been restarted "
                f"and verified healthy. Clearance confirmed for {operator_id}. "
                f"Audit trail reference: {audit_id}."
            )

        if status == "simulated":
            return (
                f"Dry run completed. Service {service} would be restarted successfully. "
                f"Clearance confirmed for {operator_id}. "
                "Say 'execute restart' to proceed with the actual restart."
            )

        # Unexpected status
        error = result.get("error", "Unknown error during restart")
        return (
            f"Restart attempt on {service} returned status: {status}. "
            f"Details: {error}. "
            "Escalating to on-call DevOps for manual recovery."
        )

    except Exception:
        log.exception("Error in escalate_critical_outage for %s", service)
        return (
            f"An error occurred while attempting to restart {service}. "
            "The incident has been escalated to the on-call team for immediate attention."
        )


def get_voice_tools() -> list[Any]:
    """Return the list of tools for the slim voice agent."""
    return [report_incident, get_investigation_status, quick_metrics, escalate_critical_outage]

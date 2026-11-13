"""Voice-optimized slim tools for the LiveKit voice agent.

Rubric Part 3 (Section 7):
The voice agent is a slim, fast agent with a few tools, not the full swarm.
Tools:
1. report_incident: creates a ticket and starts the graph asynchronously
2. get_investigation_status: reads state from DB or checkpointer
3. quick_metrics: fetches immediate service telemetry
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from livekit.agents import function_tool

from incident_swarm.mcp_servers import observability, ticketing

log = logging.getLogger("voice.tools")

# Async background runner hook for graph dispatch (can be injected or default)
_async_graph_invoker = None


def set_graph_invoker(invoker_fn):
    """Set the async callable that receives (ticket_id, summary, service)."""
    global _async_graph_invoker
    _async_graph_invoker = invoker_fn


async def _dispatch_investigation_background(ticket_id: str, summary: str, service: str | None):
    """Run the graph asynchronously in the background so voice response is not delayed."""
    if _async_graph_invoker is not None:
        try:
            if asyncio.iscoroutinefunction(_async_graph_invoker):
                await _async_graph_invoker(ticket_id, summary, service)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _async_graph_invoker, ticket_id, summary, service)
        except Exception:
            log.exception("Background graph investigation failed for ticket %s", ticket_id)
    else:
        log.info("No async graph invoker registered; ticket %s queued.", ticket_id)


@function_tool(
    name="report_incident",
    description="Report an IT outage or issue. Creates an official incident ticket and starts the swarm investigation in the background.",
)
async def report_incident(
    summary: str,
    service: str | None = None,
    reported_by: str = "voice-operator",
) -> str:
    """Creates a ticket and dispatches the background swarm."""
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
            "Our automated investigation swarm is actively running in the background."
        )
    except Exception:
        log.exception("Error in report_incident")
        return f"Created provisional voice incident for '{summary}'. Investigation dispatched."


@function_tool(
    name="get_investigation_status",
    description="Look up the status and findings for an incident ticket by its ticket ID.",
)
async def get_investigation_status(ticket_id: str) -> str:
    """Reads ticket status from the ticketing system."""
    try:
        res = ticketing.get_ticket(ticket_id=ticket_id)
        if not res or res.get("status") == "error":
            return f"Ticket {ticket_id} is currently queued in triage analysis."

        status = res.get("status", "open")
        severity = res.get("severity", "medium")
        desc = res.get("issue_description", "")
        return (
            f"Ticket {ticket_id} is currently {status} with {severity} severity. "
            f"Summary: {desc[:100]}."
        )
    except Exception:
        log.exception("Error in get_investigation_status")
        return f"Ticket {ticket_id} is being processed by the investigation agent."


@function_tool(
    name="quick_metrics",
    description="Fetch live CPU, memory, and error rate telemetry metrics for an infrastructure service.",
)
async def quick_metrics(service_name: str) -> str:
    """Fetches fast telemetry from Observability MCP."""
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


def get_voice_tools() -> list[Any]:
    """Return the list of tools for the slim voice agent."""
    return [report_incident, get_investigation_status, quick_metrics]

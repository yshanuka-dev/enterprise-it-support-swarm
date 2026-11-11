"""Shared state for the LangGraph swarm (the single object every node reads and writes)."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

TicketType = Literal[
    "access_identity", "asset_provisioning", "service_degradation", "critical_outage", "unknown"
]
Severity = Literal["low", "medium", "critical", "unknown"]


class SwarmState(TypedDict, total=False):
    """State carried through the graph and persisted per session by the checkpointer."""

    # identity
    ticket_id: str
    session_id: str
    channel: Literal["chat", "voice"]
    messages: Annotated[list, add_messages]
    user_id: str | None
    asset_id: str | None

    # L1 triage
    ticket_type: TicketType
    severity: Severity
    service: str | None
    triage_confidence: float
    clarify_attempts: int  # persists across turns

    # L2 investigation
    evidence: dict
    tool_error: str | None
    tool_retry_count: int

    # L3 resolution
    runbook_citations: list[dict]
    proposed_action: str | None
    clearance_ok: bool | None
    resolution_summary: str | None
    needs_voice_escalation: bool
    rma_number: str | None

    # control and output (reset every turn by the intake node)
    path: list[str]
    hop_count: int
    cache_hit: bool
    escalated_to_human: bool
    escalation_reason: str | None
    reply: str

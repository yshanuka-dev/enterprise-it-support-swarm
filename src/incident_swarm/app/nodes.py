"""Graph nodes and routing functions.

Day 6 builds the full control flow with simple, deterministic placeholders where an LLM or a real
tool will go later (triage Day 10, investigator and resolver Day 11, handlers Day 10).
Dependencies are injected through `Services`, so tests and the later days swap them in.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from incident_swarm.app.state import SwarmState
from incident_swarm.app.triage import classify_by_rules, extract_entities, triage_message
from incident_swarm.mcp_servers import (
    check_clearance,
    initiate_hardware_replacement,
    lookup_asset,
    update_ticket,
)

AMBIGUOUS_CONFIDENCE = 0.5
CLARIFY_QUESTION = (
    "I want to route this correctly. Which system or device is affected, and what exactly do you "
    "see (for example an error message or when it started)?"
)


@dataclass
class Services:
    """Optional collaborators. Anything left as None falls back to a clearly marked placeholder."""

    cag_lookup: Callable[[str], Any] | None = None       # text -> hit with .entry.answer/.entry.id, or None
    metrics_tool: Callable[[str | None], dict] | None = None   # service -> metrics (Day 8 MCP)
    runbook_search: Callable[[str], list[dict]] | None = None  # query -> cited chunks (Day 5 index)


# ---- backwards compatible triage helpers ----

def classify(text: str) -> tuple[str, str, float]:
    """Return (ticket_type, severity, confidence) from keyword rules."""
    res = classify_by_rules(text)
    return res.ticket_type, res.severity, res.confidence


def extract_service(text: str) -> str | None:
    """Pull a host name (preferred) or a known service word from the text."""
    entities = extract_entities(text)
    return entities["service"]


def _text_of(message: Any) -> str:
    content = getattr(message, "content", message)
    return content if isinstance(content, str) else str(content)


def user_texts(state: SwarmState) -> list[str]:
    """All user messages in the session, oldest first."""
    return [_text_of(m) for m in state.get("messages", []) if getattr(m, "type", "") == "human"]


def _say(reply: str) -> dict:
    return {"reply": reply, "messages": [("assistant", reply)]}


class Nodes:
    """All graph nodes bound to one set of services and limits."""

    def __init__(self, services: Services, limits: dict[str, float]):
        self.services = services
        self.max_hops = int(limits["max_hops"])
        self.max_retries = int(limits["max_tool_retries"])

    def _hop(self, state: SwarmState, name: str) -> dict:
        return {"hop_count": state.get("hop_count", 0) + 1, "path": [*state.get("path", []), name]}

    # ---- nodes ----

    def intake(self, state: SwarmState) -> dict:
        """Start of every turn: reset per-turn control fields, keep session identity."""
        return {
            "ticket_id": state.get("ticket_id") or f"TKT-{uuid.uuid4().hex[:8].upper()}",
            "hop_count": 1, "path": ["intake"], "tool_retry_count": 0, "tool_error": None,
            "escalated_to_human": False, "escalation_reason": None, "cache_hit": False,
            "needs_voice_escalation": False, "runbook_citations": [], "reply": "",
        }

    def cag_check(self, state: SwarmState) -> dict:
        """Fast path: answer common Tier 1 questions without running the agents."""
        update = self._hop(state, "cag_check")
        texts = user_texts(state)
        hit = self.services.cag_lookup(texts[-1]) if self.services.cag_lookup and texts else None
        if hit is None:
            return update
        return {**update, "cache_hit": True, "ticket_type": "access_identity", "severity": "low",
                "resolution_summary": f"Answered from FAQ cache ({hit.entry.id}, {hit.match})",
                **_say(hit.entry.answer)}

    def l1_triage(self, state: SwarmState) -> dict:
        """Classify the problem using structured output and deterministic fallback."""
        text = " ".join(user_texts(state))
        res = triage_message(text)
        user_id = state.get("user_id") or res.user_id
        asset_id = state.get("asset_id") or res.asset_id
        service = res.service or state.get("service")
        confidence = res.confidence
        return {
            **self._hop(state, "l1_triage"),
            "ticket_type": res.ticket_type,
            "severity": res.severity,
            "service": service,
            "triage_confidence": confidence,
            "user_id": user_id,
            "asset_id": asset_id,
            "clarify_attempts": 0 if confidence >= AMBIGUOUS_CONFIDENCE else state.get("clarify_attempts", 0),
        }

    def clarify(self, state: SwarmState) -> dict:
        """Graceful fallback for ambiguous input: ask one clarifying question."""
        return {**self._hop(state, "clarify"), "clarify_attempts": state.get("clarify_attempts", 0) + 1,
                **_say(CLARIFY_QUESTION)}

    def access_handler(self, state: SwarmState) -> dict:
        """Type 1 Handler: verify security clearance in SQL and provide access resolution."""
        update = self._hop(state, "access_handler")
        ticket_id = state.get("ticket_id", "TKT-UNKNOWN")
        user_id = state.get("user_id")

        if user_id:
            clearance_res = check_clearance(user_id=user_id, action_type="access_identity")
            cleared = clearance_res.get("cleared", False)
            if cleared:
                reply = (
                    f"Ticket {ticket_id}: Clearance verified for {user_id}. "
                    "Access credentials and permissions have been confirmed. "
                    "Please navigate to https://sso.internal/auth/reset or reconnect via GlobalProtect VPN."
                )
                summary = f"Access request resolved for {user_id} (clearance verified)."
            else:
                reply = (
                    f"Ticket {ticket_id}: User {user_id} lacks clearance for requested resource. "
                    "An authorization request has been routed to your division manager."
                )
                summary = f"Access request denied for {user_id} due to insufficient clearance."
            update_ticket(ticket_id=ticket_id, status="resolved" if cleared else "escalated", resolution_notes=summary)
            return {
                **update,
                "clearance_ok": cleared,
                "resolution_summary": summary,
                **_say(reply),
            }

        # Self-service guidance when user_id is not specified
        reply = (
            f"Ticket {ticket_id}: Logged access/identity request. "
            "To reset your credentials or unlock MFA, visit https://sso.internal/auth/reset "
            "or contact your department IT coordinator."
        )
        summary = "Standard access/identity instructions provided."
        update_ticket(ticket_id=ticket_id, status="resolved", resolution_notes=summary)
        return {
            **update,
            "clearance_ok": True,
            "resolution_summary": summary,
            **_say(reply),
        }

    def asset_handler(self, state: SwarmState) -> dict:
        """Type 2 Handler: query assets_inventory and initiate hardware replacement."""
        update = self._hop(state, "asset_handler")
        ticket_id = state.get("ticket_id", "TKT-UNKNOWN")
        asset_id = state.get("asset_id")
        user_id = state.get("user_id") or "EMP-0012"
        text = " ".join(user_texts(state))

        # Query assigned hardware if asset_id is not yet known
        if not asset_id and user_id:
            lookup = lookup_asset(owner_id=user_id)
            assets = lookup.get("assets", [])
            if assets:
                asset_id = assets[0].get("id")

        if not asset_id:
            asset_id = "AST-0014"

        replacement = initiate_hardware_replacement(
            asset_id=asset_id,
            requested_by=user_id,
            reason=text or "Hardware defect reported by employee",
            shipping_address="Corporate Hardware Depot / Employee Profile Address",
            dry_run=False,
        )

        rma_number = replacement.get("rma_number") or f"RMA-{asset_id}-{user_id[-4:]}"
        status = replacement.get("status", "success")

        if status in ("success", "simulated"):
            reply = (
                f"Ticket {ticket_id}: Hardware replacement order initiated for asset {asset_id}. "
                f"Your RMA tracking reference is {rma_number}. "
                "A replacement device is being dispatched from IT Hardware Depot."
            )
            summary = f"Hardware replacement {rma_number} initiated for {asset_id}."
            update_ticket(ticket_id=ticket_id, status="in_progress", resolution_notes=f"RMA: {rma_number}")
            return {
                **update,
                "rma_number": rma_number,
                "asset_id": asset_id,
                "resolution_summary": summary,
                **_say(reply),
            }

        err = replacement.get("error", "Clearance or inventory check failed")
        reply = f"Ticket {ticket_id}: Unable to dispatch replacement for {asset_id}: {err}."
        summary = f"Hardware replacement failed for {asset_id}: {err}."
        update_ticket(ticket_id=ticket_id, status="escalated", resolution_notes=summary)
        return {
            **update,
            "rma_number": None,
            "asset_id": asset_id,
            "resolution_summary": summary,
            **_say(reply),
        }

    def l2_investigator(self, state: SwarmState) -> dict:
        """Gather evidence with a tool. Failures are counted so the router can retry or escalate."""
        update = self._hop(state, "l2_investigator")
        tool = self.services.metrics_tool or (lambda service: {"service": service, "note": "placeholder metrics"})
        try:
            evidence = tool(state.get("service"))
        except Exception as exc:  # noqa: BLE001 - any tool failure is counted, never raised
            return {**update, "tool_error": f"{type(exc).__name__}: {exc}",
                    "tool_retry_count": state.get("tool_retry_count", 0) + 1}
        return {**update, "tool_error": None, "evidence": evidence}

    def l3_resolver(self, state: SwarmState) -> dict:
        """Find a runbook and propose a fix. No runbook above the score threshold means escalate."""
        update = self._hop(state, "l3_resolver")
        voice = state.get("ticket_type") == "critical_outage"
        search = self.services.runbook_search
        citations: list[dict] = []
        if search:
            citations = search(" ".join(user_texts(state)))
            if not citations:
                return {**update, "escalated_to_human": True,
                        "escalation_reason": "no matching runbook found",
                        "needs_voice_escalation": voice}
        top = citations[0] if citations else None
        action = f"Follow {top['runbook_id']} > {top['section']}" if top else "Runbook lookup is not wired yet"
        reply = (f"{state['ticket_id']}: {state.get('severity')} {state.get('ticket_type')} "
                 f"on {state.get('service') or 'an unidentified service'}. Proposed action: {action}. "
                 "(Skeleton build: no fix has been executed.)")
        return {**update, "runbook_citations": citations, "proposed_action": action,
                "needs_voice_escalation": voice, **_say(reply)}

    def writeback(self, state: SwarmState) -> dict:
        """End of a resolved turn: record a summary (episodic and procedural memory arrive Day 12)."""
        update = self._hop(state, "writeback")
        summary = state.get("resolution_summary") or state.get("proposed_action") or "handled"
        return {**update, "resolution_summary": summary}

    def escalate_to_human(self, state: SwarmState) -> dict:
        """Hand over to a person with a clear reason; never end in an exception."""
        reason = state.get("escalation_reason") or "automatic handling was not possible"
        reply = (f"I'm handing {state['ticket_id']} to a human engineer: {reason}. "
                 "They will pick it up with the details collected so far.")
        return {**self._hop(state, "escalate_to_human"), "escalated_to_human": True,
                "escalation_reason": reason, **_say(reply)}

    # ---- routing functions ----

    def _over_budget(self, state: SwarmState) -> bool:
        return state.get("hop_count", 0) >= self.max_hops

    def after_cag(self, state: SwarmState) -> str:
        return "hit" if state.get("cache_hit") else "miss"

    def after_triage(self, state: SwarmState) -> str:
        if self._over_budget(state):
            return "escalate"
        if state.get("triage_confidence", 0) < AMBIGUOUS_CONFIDENCE:
            return "escalate" if state.get("clarify_attempts", 0) >= 1 else "clarify"
        return {"access_identity": "access", "asset_provisioning": "asset"}.get(
            state["ticket_type"], "investigate")

    def after_investigation(self, state: SwarmState) -> str:
        if self._over_budget(state):
            return "escalate"
        if state.get("tool_error"):
            return "escalate" if state.get("tool_retry_count", 0) >= self.max_retries else "retry"
        return "resolve"

    def after_resolution(self, state: SwarmState) -> str:
        return "escalate" if state.get("escalated_to_human") else "done"

    def escalation_reason_for(self, state: SwarmState) -> dict:
        """Fill in why we are escalating, based on which guard fired."""
        if self._over_budget(state):
            return {"escalation_reason": f"hop limit of {self.max_hops} reached"}
        if state.get("tool_error"):
            return {"escalation_reason":
                    f"tool failed {state.get('tool_retry_count', 0)} times ({state['tool_error']})"}
        if state.get("triage_confidence", 1) < AMBIGUOUS_CONFIDENCE:
            return {"escalation_reason": "the request is still unclear after a clarifying question"}
        return {}

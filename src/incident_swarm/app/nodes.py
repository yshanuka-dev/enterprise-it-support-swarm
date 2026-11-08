"""Graph nodes and routing functions.

Day 6 builds the full control flow with simple, deterministic placeholders where an LLM or a real
tool will go later (triage Day 10, investigator and resolver Day 11, handlers Day 10).
Dependencies are injected through `Services`, so tests and the later days swap them in.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from incident_swarm.app.state import SwarmState

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


# ---- triage rules (placeholder for the Day 10 LLM triage) ----

TRIAGE_RULES: tuple[tuple[str, str, re.Pattern], ...] = (
    ("critical_outage", "critical", re.compile(
        r"\b(out of memory|oom|outage|is down|are down|502|503|crash(?:ed|ing)?|unresponsive|"
        r"not responding)\b", re.IGNORECASE)),
    ("service_degradation", "medium", re.compile(
        r"\b(slow|latency|lag|degrad\w*|timeouts?|timing out|delayed|intermittent|high cpu)\b", re.IGNORECASE)),
    ("asset_provisioning", "low", re.compile(
        r"\b(laptop|monitor|keyboard|mouse|dock(?:ing)?|screen|headset|hardware|replacement|refresh|"
        r"ssd|ram)\b", re.IGNORECASE)),
    ("access_identity", "low", re.compile(
        r"\b(vpn|password|log ?in|locked out|mfa|2fa|authenticator|access|permissions?|ssh|sso|"
        r"account)\b", re.IGNORECASE)),
)
SERVICE_WORDS = re.compile(
    r"\b(redis|postgres|database|auth|checkout|vpn|kubernetes|elasticsearch|smtp|email|api)\b", re.IGNORECASE)
HOST_PATTERN = re.compile(r"\b(?:prod|staging)-[a-z]+-\d+\b", re.IGNORECASE)


def classify(text: str) -> tuple[str, str, float]:
    """Return (ticket_type, severity, confidence) from keyword rules."""
    matched = [(t, s) for t, s, pattern in TRIAGE_RULES if pattern.search(text)]
    if not matched:
        return "unknown", "unknown", 0.2
    ticket_type, severity = matched[0]
    return ticket_type, severity, 0.9 if len(matched) == 1 else 0.6


def extract_service(text: str) -> str | None:
    """Pull a host name (preferred) or a known service word from the text."""
    host = HOST_PATTERN.search(text)
    if host:
        return host.group(0).lower()
    word = SERVICE_WORDS.search(text)
    return word.group(0).lower() if word else None


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
        """Classify the problem using everything the user has said in this session."""
        text = " ".join(user_texts(state))
        ticket_type, severity, confidence = classify(text)
        return {**self._hop(state, "l1_triage"), "ticket_type": ticket_type, "severity": severity,
                "service": extract_service(text), "triage_confidence": confidence,
                "clarify_attempts": 0 if confidence >= AMBIGUOUS_CONFIDENCE else state.get("clarify_attempts", 0)}

    def clarify(self, state: SwarmState) -> dict:
        """Graceful fallback for ambiguous input: ask one clarifying question."""
        return {**self._hop(state, "clarify"), "clarify_attempts": state.get("clarify_attempts", 0) + 1,
                **_say(CLARIFY_QUESTION)}

    def access_handler(self, state: SwarmState) -> dict:
        """Type 1 placeholder: the real version checks clearance in SQL (Day 10)."""
        reply = (f"I've logged {state['ticket_id']} as an access and identity request. "
                 "(Skeleton build: the clearance check and fix are not wired yet.)")
        return {**self._hop(state, "access_handler"), **_say(reply)}

    def asset_handler(self, state: SwarmState) -> dict:
        """Type 2 placeholder: the real version checks assets_inventory (Day 10)."""
        reply = (f"I've logged {state['ticket_id']} as a hardware request. "
                 "(Skeleton build: the assets_inventory lookup is not wired yet.)")
        return {**self._hop(state, "asset_handler"), **_say(reply)}

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

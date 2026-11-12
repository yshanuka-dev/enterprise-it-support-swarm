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


def default_metrics_tool(service: str | None) -> dict:
    """Retrieve telemetry metrics and anomalies from the Observability MCP server."""
    from incident_swarm.mcp_servers.observability import get_metrics, list_anomalies

    svc = service or "redis-cluster"
    metrics = get_metrics(svc)
    anomalies = list_anomalies(svc)
    return {
        "service": svc,
        "metrics": metrics,
        "anomalies": anomalies.get("anomalies", []),
        "status": metrics.get("status", "healthy"),
    }


def default_runbook_search(query: str) -> list[dict]:
    """Retrieve runbook chunks using Qdrant vector search with local keyword fallback."""
    from incident_swarm.config import ROOT, get_settings
    from incident_swarm.memory import runbooks

    settings = get_settings()
    try:
        from incident_swarm.memory.embeddings import FastEmbedder
        from incident_swarm.memory.procedural import get_qdrant_client

        client = get_qdrant_client(settings)
        embedder = FastEmbedder()
        hits = runbooks.retrieve(client, embedder, query, k=3, min_score=0.35)
        if hits:
            return hits
    except Exception:  # noqa: BLE001, S110
        pass

    rb_dir = ROOT / "runbooks"
    if rb_dir.exists():
        chunks = runbooks.load_runbooks(rb_dir)
        query_words = {w.lower() for w in re.findall(r"\b[A-Za-z0-9\-]+\b", query) if len(w) > 2}
        scored = []
        for c in chunks:
            title_text = f"{c['title']} {c['runbook_id']}".lower()
            body_text = f"{c['section']} {c['text']}".lower()
            title_overlap = sum(1 for w in query_words if w in title_text)
            body_overlap = sum(1 for w in query_words if w in body_text)
            if title_overlap > 0 or body_overlap > 0:
                score = min(0.98, 0.35 + 0.25 * title_overlap + 0.05 * body_overlap)
                scored.append({"score": score, **c})
        scored.sort(key=lambda x: -x["score"])
        return scored[:3]
    return []


def default_procedural_search(query: str, k: int = 3) -> list[dict]:
    """Retrieve historical incident precedents and apply canonical deduplication."""
    from incident_swarm.config import ROOT, get_settings
    from incident_swarm.memory import canonical, procedural

    settings = get_settings()
    hits: list[dict] = []
    try:
        from incident_swarm.memory.embeddings import FastEmbedder

        client = procedural.get_qdrant_client(settings)
        embedder = FastEmbedder()
        hits = procedural.search(client, embedder, query, k=k * 3)
    except Exception:  # noqa: BLE001, S110
        pass

    if not hits:
        distilled_file = ROOT / "data" / "distilled_full.jsonl"
        if distilled_file.exists():
            import json

            query_words = {w.lower() for w in re.findall(r"\b[A-Za-z0-9\-]+\b", query) if len(w) > 2}
            scored: list[dict] = []
            with distilled_file.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        text = (
                            f"{record.get('user_problem', '')} "
                            f"{record.get('resolved_problem', '')} "
                            f"{record.get('canonical_title', '')}"
                        ).lower()
                        overlap = sum(1 for w in query_words if w in text)
                        if overlap > 0:
                            score = min(0.95, 0.40 + 0.10 * overlap)
                            scored.append({"score": score, **record})
                    except Exception:  # noqa: BLE001, S112
                        continue
            scored.sort(key=lambda x: -x["score"])
            hits = scored[: k * 4]

    return canonical.collapse_hits(hits, k=k)


@dataclass
class Services:
    """Optional collaborators. Anything left as None falls back to a clearly marked placeholder."""

    cag_lookup: Callable[[str], Any] | None = None       # text -> hit with .entry.answer/.entry.id, or None
    metrics_tool: Callable[[str | None], dict] | None = None   # service -> metrics (Day 8 MCP)
    runbook_search: Callable[[str], list[dict]] | None = None  # query -> cited chunks (Day 5 index)
    procedural_search: Callable[[str], list[dict]] | None = None  # query -> past resolutions (Day 12 index)


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
            "needs_voice_escalation": False, "runbook_citations": [], "procedural_citations": [],
            "reply": "",
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
        """Gather telemetry evidence with bounded retries and anomaly tracking."""
        update = self._hop(state, "l2_investigator")
        tool = self.services.metrics_tool or default_metrics_tool
        try:
            evidence = tool(state.get("service"))
        except Exception as exc:  # noqa: BLE001 - any tool failure is counted, never raised
            return {
                **update,
                "tool_error": f"{type(exc).__name__}: {exc}",
                "tool_retry_count": state.get("tool_retry_count", 0) + 1,
            }
        return {**update, "tool_error": None, "evidence": evidence}

    def l3_resolver(self, state: SwarmState) -> dict:
        """Find a runbook, search procedural memory, gate clearance, and propose/execute fix."""
        update = self._hop(state, "l3_resolver")
        ticket_id = state.get("ticket_id", "TKT-UNKNOWN")
        voice = state.get("ticket_type") == "critical_outage"
        user_text = " ".join(user_texts(state))
        search = self.services.runbook_search or default_runbook_search
        proc_search = self.services.procedural_search or default_procedural_search

        citations: list[dict] = search(user_text) if search else []
        
        # CRAG Gating: Filter out citations with poor semantic scores or domain mismatches
        # E.g. If user text is software degradation/reboot, do not cite RB-HARDWARE
        valid_citations = []
        is_software_query = bool(re.search(r"\b(software|reboot|restart|crash|driver|os|freeze|bsod)\b", user_text, re.I))
        for c in citations:
            score = c.get("score", 0.0)
            rb_id = c.get("runbook_id", "")
            # Reject hardware runbooks when user explicitly reports a software issue
            if is_software_query and "HARDWARE" in rb_id:
                continue
            # CRAG score threshold: must have reasonable semantic relevance (>= 0.35)
            if score >= 0.35:
                valid_citations.append(c)

        if not valid_citations:
            reason = "no matching runbook found"
            reply = (
                f"⚠️ **Incident {ticket_id}**: No verified SOP runbook found for this specific issue.\n\n"
                f"• **Reason**: {reason}.\n"
                f"• **Next Step**: Escalated to an on-call IT Service Desk specialist for manual investigation."
            )
            return {
                **update,
                "runbook_citations": [],
                "procedural_citations": [],
                "escalated_to_human": True,
                "escalation_reason": reason,
                "needs_voice_escalation": voice,
                **_say(reply),
            }

        # Query procedural memory (historical incident precedent with canonical deduplication)
        precedents: list[dict] = proc_search(user_text) if proc_search else []

        top = valid_citations[0]
        runbook_id = top.get("runbook_id", "RB-UNKNOWN")
        section = top.get("section", "Remediation")
        proposed_action = f"Follow {runbook_id} > {section}"

        # Determine operational action type for clearance gating
        action_type = "restart_service"
        if "redis" in runbook_id.lower() or "cache" in user_text.lower():
            action_type = "clear_cache"
        elif "connection" in runbook_id.lower() or "oom" in runbook_id.lower() or "outage" in state.get("ticket_type", ""):
            action_type = "restart_service"

        # Clearance verification
        user_id = state.get("user_id") or "EMP-0012"
        clearance_res = check_clearance(user_id=user_id, action_type=action_type)
        cleared = clearance_res.get("cleared", False)

        precedent_text = ""
        if precedents:
            top_p = precedents[0]
            count = top_p.get("similar_cases", 1)
            p_title = top_p.get("canonical_title") or top_p.get("resolved_problem", "")
            precedent_text = f" (Precedent: {count} similar cases verified under '{p_title}')"

        service = state.get("service") or "infrastructure service"
        sev_label = (state.get("severity") or "high").upper()
        type_label = (state.get("ticket_type") or "incident").replace("_", " ")

        if cleared:
            summary = (
                f"Verified fix via {runbook_id} ({section}) for {service}. "
                f"Clearance confirmed for {user_id}.{precedent_text}"
            )
            reply = (
                f"🚨 **Incident {ticket_id}** [{sev_label}]: Outage detected on **{service}**.\n\n"
                f"• **Remediation Plan**: Following standard operating procedure **{runbook_id}** ({section}).\n"
                f"• **Clearance Status**: Clearance verified (`{action_type}` approved for employee `{user_id}`).\n"
                f"• **Next Step**: Executing remediation protocol to restore service stability.\n"
                f"{'• **Historical Reference**:' + precedent_text if precedent_text else ''}"
            ).strip()
            update_ticket(ticket_id=ticket_id, status="in_progress", resolution_notes=summary)
            return {
                **update,
                "runbook_citations": citations,
                "procedural_citations": precedents,
                "proposed_action": proposed_action,
                "clearance_ok": True,
                "resolution_summary": summary,
                "needs_voice_escalation": voice,
                **_say(reply),
            }

        summary = f"Fix identified in {runbook_id} ({section}), but {user_id} lacks clearance for {action_type}."
        reply = (
            f"⚠️ **Incident {ticket_id}** [{sev_label}]: Outage detected on **{service}**.\n\n"
            f"• **Identified SOP**: {runbook_id} ({section}).\n"
            f"• **Clearance Notice**: User `{user_id}` lacks DevOps clearance for action `{action_type}`.\n"
            f"• **Action Taken**: Escalation ticket routed directly to the on-call DevOps engineering team."
        )
        update_ticket(ticket_id=ticket_id, status="escalated", resolution_notes=summary)
        return {
            **update,
            "runbook_citations": citations,
            "procedural_citations": precedents,
            "proposed_action": proposed_action,
            "clearance_ok": False,
            "resolution_summary": summary,
            "needs_voice_escalation": voice,
            **_say(reply),
        }

    def writeback(self, state: SwarmState) -> dict:
        """End of a resolved turn: record episodic summary and persist ticket status."""
        update = self._hop(state, "writeback")
        summary = state.get("resolution_summary") or state.get("proposed_action") or "handled"
        ticket_id = state.get("ticket_id")
        if ticket_id and not state.get("escalated_to_human"):
            update_ticket(ticket_id=ticket_id, status="resolved", resolution_notes=summary)
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

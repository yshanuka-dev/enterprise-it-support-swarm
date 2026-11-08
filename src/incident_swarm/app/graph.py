"""Build the multi-agent StateGraph: CAG -> L1 triage -> (handlers | L2 investigate -> L3 resolve) -> writeback.

Loop guards: tool failures are counted (retry, then escalate), every node adds to hop_count
(escalate at max_hops), and LangGraph's recursion_limit is a final backstop.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from incident_swarm.app.nodes import Nodes, Services
from incident_swarm.app.state import SwarmState

DEFAULT_LIMITS = {"max_hops": 8, "max_tool_retries": 3}


def build_graph(services: Services | None = None, limits: dict[str, float] | None = None,
                checkpointer=None):
    """Compile the swarm. The checkpointer keeps per-session state (MemorySaver now, Postgres later)."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    nodes = Nodes(services or Services(), limits)
    graph = StateGraph(SwarmState)

    for name in ("intake", "cag_check", "l1_triage", "clarify", "access_handler", "asset_handler",
                 "l2_investigator", "l3_resolver", "writeback"):
        graph.add_node(name, getattr(nodes, name))

    def escalate(state: SwarmState) -> dict:
        update = nodes.escalation_reason_for(state)
        return nodes.escalate_to_human({**state, **update}) | update

    graph.add_node("escalate_to_human", escalate)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "cag_check")
    graph.add_conditional_edges("cag_check", nodes.after_cag, {"hit": END, "miss": "l1_triage"})
    graph.add_conditional_edges("l1_triage", nodes.after_triage, {
        "clarify": "clarify", "access": "access_handler", "asset": "asset_handler",
        "investigate": "l2_investigator", "escalate": "escalate_to_human"})
    graph.add_conditional_edges("l2_investigator", nodes.after_investigation, {
        "retry": "l2_investigator", "resolve": "l3_resolver", "escalate": "escalate_to_human"})
    graph.add_conditional_edges("l3_resolver", nodes.after_resolution, {
        "done": "writeback", "escalate": "escalate_to_human"})
    for terminal in ("clarify", "access_handler", "asset_handler"):
        graph.add_edge(terminal, "writeback" if terminal != "clarify" else END)
    graph.add_edge("writeback", END)
    graph.add_edge("escalate_to_human", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())

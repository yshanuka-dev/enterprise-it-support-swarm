"""Unit and integration tests for Day 12: Episodic and Procedural Memory.

Tests:
- Postgres/Memory checkpointer factory and multi-turn state persistence.
- Procedural memory search with canonical incident deduplication.
- Precedent-enriched resolution in L3 Resolver.
"""

from tests.test_graph import run

from incident_swarm.app.checkpoint import get_checkpointer
from incident_swarm.app.graph import build_graph
from incident_swarm.app.nodes import Nodes, Services, default_procedural_search
from incident_swarm.config import Settings
from incident_swarm.memory import canonical


def test_get_checkpointer_returns_valid_saver():
    """Verify checkpointer factory returns a valid checkpointer instance."""
    saver = get_checkpointer(Settings.model_construct(database_url="postgresql://invalid:5432/bad_db"))
    assert saver is not None


def test_episodic_memory_persists_state_across_turns():
    """Verify multi-turn session persistence using the checkpointer."""
    graph = build_graph()
    session = "multi-turn-session-12"

    first = run(graph, "My laptop screen is broken", session=session)
    assert first["path"][-1] == "writeback"
    assert first["ticket_type"] == "asset_provisioning"
    ticket_id = first["ticket_id"]

    # Second turn in the same session must retain the ticket_id and extend messages
    second = run(graph, "Also need a docking station", session=session)
    assert second["ticket_id"] == ticket_id
    assert len(second["messages"]) >= 4


def test_default_procedural_search_collapses_duplicates():
    """Verify canonical deduplication folds near-duplicate past incident transcripts."""
    precedents = default_procedural_search("Redis out of memory noeviction", k=2)
    assert len(precedents) > 0
    top = precedents[0]
    assert top["canonical_id"] == "redis_oom"
    assert top["canonical_title"] == "Redis out of memory (noeviction policy)"
    assert top["runbook_id"] == "RB-REDIS-OOM"
    assert top.get("similar_cases", 1) >= 1


def test_canonical_collapse_hits_aggregation():
    """Verify collapse_hits folds raw transcripts by canonical_id."""
    raw_hits = [
        {"transcript_id": "T-1", "canonical_id": "auth_oom", "score": 0.88, "text": "Auth pod crash 1"},
        {"transcript_id": "T-2", "canonical_id": "auth_oom", "score": 0.92, "text": "Auth pod crash 2"},
        {"transcript_id": "T-3", "canonical_id": "auth_oom", "score": 0.75, "text": "Auth pod crash 3"},
        {"transcript_id": "T-4", "canonical_id": "redis_oom", "score": 0.80, "text": "Redis full"},
    ]
    collapsed = canonical.collapse_hits(raw_hits, k=2)
    assert len(collapsed) == 2
    # Top should be auth_oom with highest score 0.92 and similar_cases == 3
    assert collapsed[0]["canonical_id"] == "auth_oom"
    assert collapsed[0]["score"] == 0.92
    assert collapsed[0]["similar_cases"] == 3
    # Second should be redis_oom with similar_cases == 1
    assert collapsed[1]["canonical_id"] == "redis_oom"
    assert collapsed[1]["similar_cases"] == 1


def test_l3_resolver_enriches_reply_with_procedural_precedents():
    """Verify L3 Resolver attaches procedural citations and precedent text to response."""
    mock_runbook = [{"runbook_id": "RB-REDIS-OOM", "section": "Remediation", "score": 0.9}]
    mock_procedural = [
        {
            "canonical_id": "redis_oom",
            "canonical_title": "Redis out of memory (noeviction policy)",
            "similar_cases": 14,
            "runbook_id": "RB-REDIS-OOM",
        }
    ]
    nodes = Nodes(
        Services(runbook_search=lambda _: mock_runbook, procedural_search=lambda _: mock_procedural),
        {"max_hops": 8, "max_tool_retries": 3},
    )
    state = {
        "ticket_id": "TKT-DAY12-TEST",
        "user_id": "EMP-0012",
        "ticket_type": "critical_outage",
        "severity": "critical",
        "service": "prod-cache-01",
        "messages": [("user", "Redis OOM error")],
        "path": ["intake", "l1_triage", "l2_investigator"],
    }
    update = nodes.l3_resolver(state)
    assert len(update["procedural_citations"]) > 0
    assert "Precedent: 14 similar cases" in update["reply"]
    assert "Redis out of memory" in update["reply"]
    assert update["clearance_ok"] is True

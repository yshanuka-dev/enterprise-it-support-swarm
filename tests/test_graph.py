"""Graph behaviour: routing, multi-turn state, loop guards and fallbacks."""

from pathlib import Path

import pytest

from incident_swarm.app import tracing
from incident_swarm.app.graph import build_graph
from incident_swarm.app.nodes import Services, classify, extract_service
from incident_swarm.memory import cag

ROOT = Path(__file__).parents[1]


def run(graph, text, session="s1", channel="chat", max_hops=8):
    config = tracing.run_config(session, channel, max_hops)
    return graph.invoke({"messages": [("user", text)], "session_id": session, "channel": channel}, config)


@pytest.fixture
def faq_services():
    entries, _ = cag.load_faq(ROOT / "config" / "faq.yaml")
    return Services(cag_lookup=cag.FaqCache(entries).lookup)


def test_classify_and_extract_service():
    assert classify("Redis is running out of memory on prod-cache-01")[:2] == ("critical_outage", "critical")
    assert classify("Why is the checkout API so slow today?")[:2] == ("service_degradation", "medium")
    assert classify("My laptop screen has dead pixels")[0] == "asset_provisioning"
    assert classify("I forgot my password")[0] == "access_identity"
    assert classify("it's broken")[2] < 0.5
    assert extract_service("Redis is out of memory on prod-cache-01") == "prod-cache-01"
    assert extract_service("checkout is slow") == "checkout"


def test_faq_question_takes_the_cag_fast_path(faq_services):
    state = run(build_graph(faq_services), "How do I reset my VPN?")
    assert state["cache_hit"] and state["path"] == ["intake", "cag_check"]
    assert "disconnect" in state["reply"].lower()


def test_critical_outage_flows_through_all_three_levels():
    state = run(build_graph(), "Redis is running out of memory on prod-cache-01")
    assert state["path"] == ["intake", "cag_check", "l1_triage", "l2_investigator", "l3_resolver", "writeback"]
    assert state["ticket_type"] == "critical_outage" and state["service"] == "prod-cache-01"
    assert state["needs_voice_escalation"] is True and not state["escalated_to_human"]


def test_degradation_does_not_request_voice_escalation():
    state = run(build_graph(), "Why is the checkout API so slow today?")
    assert state["ticket_type"] == "service_degradation" and state["needs_voice_escalation"] is False
    assert "l3_resolver" in state["path"]


@pytest.mark.parametrize("text,handler", [
    ("I forgot my password", "access_handler"), ("My laptop screen has dead pixels", "asset_handler")])
def test_tier1_and_tier2_use_their_own_handlers(text, handler):
    state = run(build_graph(), text)
    assert handler in state["path"] and "l2_investigator" not in state["path"]


def test_ambiguous_input_asks_then_escalates():
    graph = build_graph()
    first = run(graph, "it's broken", session="amb")
    assert first["path"][-1] == "clarify" and "which system" in first["reply"].lower()
    second = run(graph, "still broken", session="amb")
    assert second["escalated_to_human"] and "unclear" in second["escalation_reason"]


def test_clarification_answer_is_combined_with_earlier_turn():
    graph = build_graph()
    run(graph, "it's broken", session="mt")
    state = run(graph, "the checkout API is slow", session="mt")
    assert state["ticket_type"] == "service_degradation" and not state["escalated_to_human"]


def test_ticket_id_is_stable_within_a_session_and_differs_across_sessions():
    graph = build_graph()
    a1 = run(graph, "it's broken", session="A")
    a2 = run(graph, "the checkout API is slow", session="A")
    b1 = run(graph, "it's broken", session="B")
    assert a1["ticket_id"] == a2["ticket_id"] != b1["ticket_id"]


def test_failing_tool_is_tried_three_times_then_escalates():
    calls = []

    def broken(service):
        calls.append(service)
        raise ConnectionError("metrics backend unreachable")

    state = run(build_graph(Services(metrics_tool=broken)), "Redis is out of memory on prod-cache-01")
    assert len(calls) == 3 and state["escalated_to_human"]
    assert "tool failed 3 times" in state["escalation_reason"]
    assert state["path"][-1] == "escalate_to_human"


def test_tool_that_recovers_does_not_escalate():
    attempts = []

    def flaky(service):
        attempts.append(1)
        if len(attempts) < 2:
            raise TimeoutError("slow")
        return {"cpu_pct": 91}

    state = run(build_graph(Services(metrics_tool=flaky)), "checkout API is slow")
    assert len(attempts) == 2 and not state["escalated_to_human"] and state["evidence"] == {"cpu_pct": 91}


def test_hop_limit_escalates():
    state = run(build_graph(limits={"max_hops": 3}), "Redis is out of memory", max_hops=3)
    assert state["escalated_to_human"] and "hop limit" in state["escalation_reason"]


def test_runbook_citation_is_attached_and_missing_runbook_escalates():
    hit = [{"runbook_id": "RB-REDIS-OOM", "section": "Remediation", "score": 0.8}]
    cited = run(build_graph(Services(runbook_search=lambda q: hit)), "Redis is out of memory")
    assert cited["runbook_citations"] == hit and "RB-REDIS-OOM" in cited["reply"]
    none_found = run(build_graph(Services(runbook_search=lambda q: [])), "Redis is out of memory")
    assert none_found["escalated_to_human"] and "no matching runbook" in none_found["escalation_reason"]


def test_run_config_has_session_tags_and_optional_callbacks():
    plain = tracing.run_config("sid", "voice", 8)
    assert plain["configurable"]["thread_id"] == "sid" and plain["recursion_limit"] == 32
    assert plain["metadata"]["langfuse_session_id"] == "sid"
    assert plain["metadata"]["langfuse_tags"] == ["channel:voice"] and "callbacks" not in plain
    assert tracing.run_config("sid", "chat", 8, handler="H")["callbacks"] == ["H"]


def test_make_handler_is_none_without_keys():
    from incident_swarm.config import Settings

    assert tracing.make_handler(Settings.model_construct()) is None

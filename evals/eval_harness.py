"""Eval harness and golden scenarios (Build Plan Section 11).

Can be executed:
- In-process against the local LangGraph state machine:
    uv run python -m evals.eval_harness --local
- Remotely against a live deployed HTTP API URL (e.g. AWS ALB or local Uvicorn):
    uv run python -m evals.eval_harness --url http://127.0.0.1:8000
    uv run python -m evals.eval_harness --url http://incident-swarm-alb-330378706.ap-south-1.elb.amazonaws.com
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from incident_swarm.app import tracing
from incident_swarm.app.api import default_services
from incident_swarm.app.graph import build_graph


@dataclass
class ScenarioResult:
    """Outcome of evaluating one golden scenario."""

    scenario_id: int
    name: str
    passed: bool
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


@dataclass
class EvalSuiteResult:
    """Aggregated evaluation results."""

    total: int
    passed: int
    failed: int
    total_duration_ms: float
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def pass_rate_percent(self) -> float:
        return (self.passed / self.total * 100.0) if self.total else 0.0


class EvalRunner:
    """Runs golden scenarios either locally in-process or over HTTP."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url.rstrip("/") if base_url else None
        if not self.base_url:
            self.graph = build_graph(default_services())

    def send_turn(
        self,
        message: str,
        session_id: str | None = None,
        channel: str = "chat",
    ) -> dict[str, Any]:
        """Send one turn to the swarm and return normalized response state."""
        sid = session_id or f"eval-{uuid.uuid4().hex[:8]}"

        if self.base_url:
            with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
                res = client.post(
                    "/chat",
                    json={"message": message, "session_id": sid, "channel": channel},
                )
                if res.status_code != 200:
                    raise RuntimeError(f"HTTP {res.status_code}: {res.text}")
                return res.json()

        # In-process local execution
        config = tracing.run_config(sid, channel, max_hops=8)
        state = self.graph.invoke(
            {"messages": [("user", message)], "session_id": sid, "channel": channel},
            config,
        )
        return {
            "session_id": sid,
            "ticket_id": state.get("ticket_id"),
            "reply": state.get("reply", ""),
            "ticket_type": state.get("ticket_type"),
            "severity": state.get("severity"),
            "service": state.get("service"),
            "cache_hit": state.get("cache_hit", False),
            "escalated_to_human": state.get("escalated_to_human", False),
            "escalation_reason": state.get("escalation_reason"),
            "needs_voice_escalation": state.get("needs_voice_escalation", False),
            "runbook_citations": state.get("runbook_citations", []),
            "procedural_citations": state.get("procedural_citations", []),
            "rma_number": state.get("rma_number"),
            "path": state.get("path", []),
        }

    # -------------------------------------------------------------------------
    # Golden Scenarios (Section 11)
    # -------------------------------------------------------------------------

    def eval_scenario_1_cag_fast_path(self) -> ScenarioResult:
        """Scenario 1: 'How to reset VPN?' returns a CAG hit, no agent loop."""
        start = time.perf_counter()
        try:
            res = self.send_turn("How to reset VPN?")
            dur = (time.perf_counter() - start) * 1000.0

            passed = (
                res.get("cache_hit") is True
                and res.get("path") == ["intake", "cag_check"]
                and res.get("ticket_type") == "access_identity"
                and len(res.get("reply", "")) > 10
            )
            return ScenarioResult(
                scenario_id=1,
                name="CAG Fast-Path (VPN Reset FAQ)",
                passed=passed,
                duration_ms=dur,
                details={"cache_hit": res.get("cache_hit"), "path": res.get("path")},
            )
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000.0
            return ScenarioResult(
                scenario_id=1,
                name="CAG Fast-Path (VPN Reset FAQ)",
                passed=False,
                duration_ms=dur,
                error_message=str(exc),
            )

    def eval_scenario_2_three_level_workflow(self) -> ScenarioResult:
        """Scenario 2: 'auth-db is down' shows L1 classify, L2 metrics, L3 propose fix."""
        start = time.perf_counter()
        try:
            res = self.send_turn("auth-db is down")
            dur = (time.perf_counter() - start) * 1000.0

            path = res.get("path", [])
            passed = (
                res.get("ticket_type") == "critical_outage"
                and "l1_triage" in path
                and "l2_investigator" in path
                and "l3_resolver" in path
                and res.get("needs_voice_escalation") is True
            )
            return ScenarioResult(
                scenario_id=2,
                name="Three-Level Workflow (auth-db is down)",
                passed=passed,
                duration_ms=dur,
                details={"ticket_type": res.get("ticket_type"), "path": path},
            )
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000.0
            return ScenarioResult(
                scenario_id=2,
                name="Three-Level Workflow (auth-db is down)",
                passed=False,
                duration_ms=dur,
                error_message=str(exc),
            )

    def eval_scenario_3_runbook_retrieval(self) -> ScenarioResult:
        """Scenario 3: 'Redis is out of memory on prod-cache-01' retrieves right runbook and cites it."""
        start = time.perf_counter()
        try:
            res = self.send_turn("Redis is out of memory on prod-cache-01")
            dur = (time.perf_counter() - start) * 1000.0

            citations = res.get("runbook_citations", [])
            has_rb = any(c.get("runbook_id") == "RB-REDIS-OOM" for c in citations) or (
                "RB-REDIS-OOM" in res.get("reply", "")
            )
            passed = (
                res.get("ticket_type") == "critical_outage"
                and res.get("service") == "prod-cache-01"
                and has_rb
                and not res.get("escalated_to_human")
            )
            return ScenarioResult(
                scenario_id=3,
                name="Runbook Retrieval & Citation (RB-REDIS-OOM)",
                passed=passed,
                duration_ms=dur,
                details={
                    "service": res.get("service"),
                    "citations_count": len(citations),
                    "cited_rb": [c.get("runbook_id") for c in citations],
                },
            )
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000.0
            return ScenarioResult(
                scenario_id=3,
                name="Runbook Retrieval & Citation (RB-REDIS-OOM)",
                passed=False,
                duration_ms=dur,
                error_message=str(exc),
            )

    def eval_scenario_4_procedural_memory_recall(self) -> ScenarioResult:
        """Scenario 4: Trigger similar incidents and verify procedural memory is recalled."""
        start = time.perf_counter()
        try:
            sid = f"eval-proc-{uuid.uuid4().hex[:6]}"
            # Turn 1
            self.send_turn("Redis is out of memory on prod-cache-01", session_id=sid)
            # Turn 2: similar incident inquiry
            res = self.send_turn("The cache memory is full and rejecting keys", session_id=sid)
            dur = (time.perf_counter() - start) * 1000.0

            procedural_cites = res.get("procedural_citations", [])
            has_precedent = len(procedural_cites) > 0 or (
                "precedent" in res.get("reply", "").lower()
                or "redis" in res.get("reply", "").lower()
            )
            passed = has_precedent and not res.get("escalated_to_human")
            return ScenarioResult(
                scenario_id=4,
                name="Procedural Memory Recall (Similar Incident Precedent)",
                passed=passed,
                duration_ms=dur,
                details={"procedural_citations_count": len(procedural_cites)},
            )
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000.0
            return ScenarioResult(
                scenario_id=4,
                name="Procedural Memory Recall (Similar Incident Precedent)",
                passed=False,
                duration_ms=dur,
                error_message=str(exc),
            )

    def eval_scenario_5_ambiguity_and_fallback(self) -> ScenarioResult:
        """Scenario 5: Ambiguous input produces graceful clarification then fallback."""
        start = time.perf_counter()
        try:
            sid = f"eval-amb-{uuid.uuid4().hex[:6]}"
            # Turn 1: ambiguous input
            turn1 = self.send_turn("it's broken", session_id=sid)
            is_clarifying = turn1.get("path", [])[-1] == "clarify"

            # Turn 2: still ambiguous -> escalates gracefully to human
            turn2 = self.send_turn("still broken", session_id=sid)
            dur = (time.perf_counter() - start) * 1000.0

            passed = (
                is_clarifying
                and turn2.get("escalated_to_human") is True
                and "unclear" in (turn2.get("escalation_reason") or "").lower()
            )
            return ScenarioResult(
                scenario_id=5,
                name="Ambiguous Input & Graceful Human Escalation",
                passed=passed,
                duration_ms=dur,
                details={
                    "turn1_path": turn1.get("path"),
                    "turn2_escalated": turn2.get("escalated_to_human"),
                    "reason": turn2.get("escalation_reason"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000.0
            return ScenarioResult(
                scenario_id=5,
                name="Ambiguous Input & Graceful Human Escalation",
                passed=False,
                duration_ms=dur,
                error_message=str(exc),
            )

    def eval_scenario_6_voice_metadata_and_escalation(self) -> ScenarioResult:
        """Scenario 6: Voice channel tagging and critical outage voice escalation flag."""
        start = time.perf_counter()
        try:
            res = self.send_turn("database is down", channel="voice")
            dur = (time.perf_counter() - start) * 1000.0

            passed = (
                res.get("ticket_type") == "critical_outage"
                and res.get("needs_voice_escalation") is True
            )
            return ScenarioResult(
                scenario_id=6,
                name="Voice Channel Tagging & Voice Escalation",
                passed=passed,
                duration_ms=dur,
                details={
                    "ticket_type": res.get("ticket_type"),
                    "needs_voice_escalation": res.get("needs_voice_escalation"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000.0
            return ScenarioResult(
                scenario_id=6,
                name="Voice Channel Tagging & Voice Escalation",
                passed=False,
                duration_ms=dur,
                error_message=str(exc),
            )

    def eval_scenario_7_hardware_asset_replacement(self) -> ScenarioResult:
        """Scenario 7: Broken laptop ticket checks assets_inventory and starts replacement (RMA)."""
        start = time.perf_counter()
        try:
            msg = "EMP-0089: My laptop screen has dead pixels on AST-0014"
            res = self.send_turn(msg)
            dur = (time.perf_counter() - start) * 1000.0

            path = res.get("path", [])
            rma = res.get("rma_number") or ""
            passed = (
                res.get("ticket_type") == "asset_provisioning"
                and "asset_handler" in path
                and ("RMA-AST-0014" in rma or "RMA" in res.get("reply", ""))
            )
            return ScenarioResult(
                scenario_id=7,
                name="Hardware Asset Replacement & RMA (AST-0014)",
                passed=passed,
                duration_ms=dur,
                details={"ticket_type": res.get("ticket_type"), "path": path, "rma_number": rma},
            )
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000.0
            return ScenarioResult(
                scenario_id=7,
                name="Hardware Asset Replacement & RMA (AST-0014)",
                passed=False,
                duration_ms=dur,
                error_message=str(exc),
            )

    def run_all(self) -> EvalSuiteResult:
        """Run all 7 Section 11 golden evaluation scenarios."""
        t0 = time.perf_counter()
        scenarios = [
            self.eval_scenario_1_cag_fast_path(),
            self.eval_scenario_2_three_level_workflow(),
            self.eval_scenario_3_runbook_retrieval(),
            self.eval_scenario_4_procedural_memory_recall(),
            self.eval_scenario_5_ambiguity_and_fallback(),
            self.eval_scenario_6_voice_metadata_and_escalation(),
            self.eval_scenario_7_hardware_asset_replacement(),
        ]
        total_dur = (time.perf_counter() - t0) * 1000.0
        passed_count = sum(1 for s in scenarios if s.passed)

        return EvalSuiteResult(
            total=len(scenarios),
            passed=passed_count,
            failed=len(scenarios) - passed_count,
            total_duration_ms=total_dur,
            scenarios=scenarios,
        )


def run_all_golden_scenarios(base_url: str | None = None) -> EvalSuiteResult:
    """Entry point to run all golden evaluation scenarios."""
    runner = EvalRunner(base_url=base_url)
    return runner.run_all()


def print_report(results: EvalSuiteResult, target: str) -> None:
    """Print a clean CLI summary table of evaluation results."""
    print("=" * 80)
    print(" ENTERPRISE IT INCIDENT SWARM - GOLDEN SCENARIOS EVALUATION (SECTION 11)")
    print(f" Target: {target}")
    print("=" * 80)
    print(f"{'#':<3} | {'Scenario Name':<42} | {'Status':<6} | {'Time (ms)':<9} | Details")
    print("-" * 80)

    for s in results.scenarios:
        status_str = "PASS" if s.passed else "FAIL"
        detail_summary = (
            f"Err: {s.error_message}"
            if s.error_message
            else ", ".join(f"{k}={v}" for k, v in list(s.details.items())[:2])
        )
        print(f"{s.scenario_id:<3} | {s.name:<42} | {status_str:<6} | {s.duration_ms:>8.1f} | {detail_summary}")

    print("-" * 80)
    print(
        f" SUMMARY: {results.passed}/{results.total} Passed "
        f"({results.pass_rate_percent:.1f}%) in {results.total_duration_ms:.1f}ms"
    )
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Golden Scenarios Evaluation Harness")
    parser.add_argument("--url", default=None, help="Base URL of live API (e.g. http://127.0.0.1:8000)")
    parser.add_argument("--local", action="store_true", help="Run against local in-process graph")
    args = parser.parse_args()

    target = args.url if args.url else "Local In-Process LangGraph"
    print(f"Starting evaluation suite against {target}...")
    suite = run_all_golden_scenarios(base_url=args.url)
    print_report(suite, target=target)

    if suite.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

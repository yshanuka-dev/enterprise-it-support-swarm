"""Pytest test suite for Section 11 Golden Scenarios Eval Harness."""

from evals.eval_harness import EvalRunner, run_all_golden_scenarios


def test_scenario_1_cag_fast_path():
    runner = EvalRunner()
    result = runner.eval_scenario_1_cag_fast_path()
    assert result.passed is True, f"Scenario 1 failed: {result.error_message}"
    assert result.details["cache_hit"] is True
    assert result.details["path"] == ["intake", "cag_check"]


def test_scenario_2_three_level_workflow():
    runner = EvalRunner()
    result = runner.eval_scenario_2_three_level_workflow()
    assert result.passed is True, f"Scenario 2 failed: {result.error_message}"
    assert result.details["ticket_type"] == "critical_outage"
    assert "l2_investigator" in result.details["path"]
    assert "l3_resolver" in result.details["path"]


def test_scenario_3_runbook_retrieval():
    runner = EvalRunner()
    result = runner.eval_scenario_3_runbook_retrieval()
    assert result.passed is True, f"Scenario 3 failed: {result.error_message}"
    assert result.details["service"] == "prod-cache-01"
    assert "RB-REDIS-OOM" in result.details["cited_rb"]


def test_scenario_4_procedural_memory_recall():
    runner = EvalRunner()
    result = runner.eval_scenario_4_procedural_memory_recall()
    assert result.passed is True, f"Scenario 4 failed: {result.error_message}"
    assert result.details["procedural_citations_count"] >= 1


def test_scenario_5_ambiguity_and_fallback():
    runner = EvalRunner()
    result = runner.eval_scenario_5_ambiguity_and_fallback()
    assert result.passed is True, f"Scenario 5 failed: {result.error_message}"
    assert result.details["turn1_path"][-1] == "clarify"
    assert result.details["turn2_escalated"] is True


def test_scenario_6_voice_metadata_and_escalation():
    runner = EvalRunner()
    result = runner.eval_scenario_6_voice_metadata_and_escalation()
    assert result.passed is True, f"Scenario 6 failed: {result.error_message}"
    assert result.details["needs_voice_escalation"] is True


def test_scenario_7_hardware_asset_replacement():
    runner = EvalRunner()
    result = runner.eval_scenario_7_hardware_asset_replacement()
    assert result.passed is True, f"Scenario 7 failed: {result.error_message}"
    assert "asset_handler" in result.details["path"]
    assert "RMA-AST-0014" in result.details["rma_number"]


def test_full_eval_suite_100_percent_pass():
    suite = run_all_golden_scenarios()
    assert suite.failed == 0
    assert suite.passed == 7
    assert suite.pass_rate_percent == 100.0

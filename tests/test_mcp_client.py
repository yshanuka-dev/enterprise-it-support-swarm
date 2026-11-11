"""Unit tests for the MCP Client Helper (timeouts, retries, loop guards)."""

from __future__ import annotations

import time

from incident_swarm.mcp_servers.client import MCPClient, call_mcp_tool


def sample_successful_tool(val: int) -> dict:
    return {"status": "success", "result": val * 2}


def sample_failing_tool() -> dict:
    return {"status": "failure", "error": "Underlying system failure"}


def sample_slow_tool() -> dict:
    time.sleep(0.4)
    return {"status": "success"}


def sample_crashing_tool():
    raise RuntimeError("Unexpected internal crash")


class TestMCPClientWrapper:
    def test_successful_call(self):
        client = MCPClient()
        res = client.call_tool(sample_successful_tool, 21)
        assert res["success"] is True
        assert res["data"]["result"] == 42
        assert res["error"] is None
        assert res["retry_count"] == 0
        assert res["should_escalate"] is False
        assert res["escalation_reason"] is None

    def test_tool_returning_failure_increments_retry(self):
        client = MCPClient(max_retries=3)
        res = client.call_tool(sample_failing_tool, current_retry_count=1)
        assert res["success"] is False
        assert res["retry_count"] == 2
        assert "Underlying system failure" in res["error"]
        assert res["should_escalate"] is False

    def test_timeout_protection(self):
        client = MCPClient()
        # Enforce strict 0.05s timeout on 0.4s slow tool
        res = client.call_tool(sample_slow_tool, timeout=0.05, current_retry_count=0)
        assert res["success"] is False
        assert "timed out" in res["error"].lower()
        assert res["retry_count"] == 1
        assert res["should_escalate"] is False

    def test_exception_trapped_never_raises(self):
        client = MCPClient()
        res = client.call_tool(sample_crashing_tool, current_retry_count=0)
        assert res["success"] is False
        assert "RuntimeError" in res["error"]
        assert res["retry_count"] == 1
        assert res["should_escalate"] is False

    def test_max_retries_triggers_escalation(self):
        client = MCPClient(max_retries=3)
        # 3rd attempt failing should signal escalation
        res = client.call_tool(sample_failing_tool, current_retry_count=2)
        assert res["success"] is False
        assert res["retry_count"] == 3
        assert res["should_escalate"] is True
        assert "reached maximum retry limit" in res["escalation_reason"]

    def test_convenience_helper(self):
        res = call_mcp_tool(sample_successful_tool, 5)
        assert res["success"] is True
        assert res["data"]["result"] == 10


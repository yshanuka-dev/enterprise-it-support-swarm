"""Unified MCP Client Helper.

Wraps all tool invocations with:
1. Strict timeout protection (default: 5.0s)
2. State retry tracking and retry incrementing
3. Infinite loop prevention: triggers human escalation when retries >= max_retries
4. Uniform structured responses: catches all exceptions and never crashes the agent loop
"""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable
from typing import Any, TypedDict

log = logging.getLogger("incident_swarm.mcp_servers.client")

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RETRIES = 3


class ToolExecutionResult(TypedDict):
    """Structured response guaranteed by the MCP client wrapper."""

    success: bool
    data: Any
    error: str | None
    retry_count: int
    should_escalate: bool
    escalation_reason: str | None


class MCPClient:
    """Client wrapper providing timeout, retry guarding, and failure resilience."""

    def __init__(
        self,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.default_timeout = default_timeout
        self.max_retries = max_retries

    def call_tool(
        self,
        tool: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        current_retry_count: int = 0,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        """Execute an MCP tool function safely with timeout and retry tracking.

        Args:
            tool: The Python callable representing the MCP tool.
            *args: Positional arguments for the tool.
            timeout: Timeout in seconds (defaults to self.default_timeout).
            current_retry_count: Current retry count from LangGraph state.
            **kwargs: Keyword arguments for the tool.

        Returns:
            ToolExecutionResult with success boolean, data, and escalation signal.
        """
        tool_name = getattr(tool, "__name__", str(tool))
        effective_timeout = timeout if timeout is not None else self.default_timeout
        new_retry_count = current_retry_count

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(tool, *args, **kwargs)
                raw_result = future.result(timeout=effective_timeout)

            # Check if tool itself returned an error dictionary
            if isinstance(raw_result, dict) and raw_result.get("status") == "failure":
                error_msg = raw_result.get("error", f"Tool '{tool_name}' failed.")
                new_retry_count += 1
                should_escalate = new_retry_count >= self.max_retries
                return {
                    "success": False,
                    "data": raw_result,
                    "error": error_msg,
                    "retry_count": new_retry_count,
                    "should_escalate": should_escalate,
                    "escalation_reason": (
                        f"Tool '{tool_name}' reached maximum retry limit ({self.max_retries}) after error: {error_msg}"
                        if should_escalate
                        else None
                    ),
                }

            return {
                "success": True,
                "data": raw_result,
                "error": None,
                "retry_count": current_retry_count,
                "should_escalate": False,
                "escalation_reason": None,
            }

        except concurrent.futures.TimeoutError:
            new_retry_count += 1
            error_msg = f"Tool '{tool_name}' timed out after {effective_timeout}s."
            log.warning(error_msg)
            should_escalate = new_retry_count >= self.max_retries
            return {
                "success": False,
                "data": None,
                "error": error_msg,
                "retry_count": new_retry_count,
                "should_escalate": should_escalate,
                "escalation_reason": (
                    f"Tool '{tool_name}' exceeded timeout ({effective_timeout}s) {new_retry_count} times."
                    if should_escalate
                    else None
                ),
            }

        except Exception as exc:  # noqa: BLE001
            new_retry_count += 1
            error_msg = f"Tool '{tool_name}' raised {type(exc).__name__}: {exc}"
            log.error(error_msg)
            should_escalate = new_retry_count >= self.max_retries
            return {
                "success": False,
                "data": None,
                "error": error_msg,
                "retry_count": new_retry_count,
                "should_escalate": should_escalate,
                "escalation_reason": (
                    f"Tool '{tool_name}' reached maximum retries after repeated failures: {error_msg}"
                    if should_escalate
                    else None
                ),
            }


# Global default client instance
_default_client: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    """Return the global default MCPClient singleton."""
    global _default_client
    if _default_client is None:
        _default_client = MCPClient()
    return _default_client


def call_mcp_tool(
    tool: Callable[..., Any],
    *args: Any,
    timeout: float | None = None,
    current_retry_count: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs: Any,
) -> ToolExecutionResult:
    """Convenience helper to call an MCP tool with standard guards."""
    client = MCPClient(max_retries=max_retries)
    return client.call_tool(
        tool,
        *args,
        timeout=timeout,
        current_retry_count=current_retry_count,
        **kwargs,
    )


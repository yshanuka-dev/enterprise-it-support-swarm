"""Langfuse tracing: one trace per request, grouped by session, tagged by channel.

Tracing is optional: without Langfuse keys everything still runs, just untraced.
"""

from __future__ import annotations

import logging

from incident_swarm.config import Settings

log = logging.getLogger("tracing")


def make_handler(settings: Settings):
    """Return a Langfuse LangChain callback handler, or None if Langfuse is not configured."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        import os
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        # Langfuse v5 OpenTelemetry exporter reads LANGFUSE_HOST / KEYS from os.environ
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        os.environ["LANGFUSE_HOST"] = settings.langfuse_host

        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )  # registers the client the handler will use
        return CallbackHandler(public_key=settings.langfuse_public_key)
    except Exception as exc:  # noqa: BLE001 - tracing must never take the API down
        log.warning("Langfuse unavailable, continuing untraced: %s", exc)
        return None


def run_config(session_id: str, channel: str, max_hops: int, handler=None) -> dict:
    """LangGraph run config: thread id for memory, recursion backstop, Langfuse callbacks and metadata."""
    config: dict = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": max_hops * 4,
        "metadata": {"langfuse_session_id": session_id, "langfuse_tags": [f"channel:{channel}"]},
    }
    if handler is not None:
        config["callbacks"] = [handler]
    return config


def flush() -> None:
    """Send any buffered traces (call on shutdown)."""
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:  # noqa: BLE001
        log.debug("Langfuse flush skipped: %s", exc)

"""Checkpointer factory for episodic memory across turns and sessions.

Uses PostgreSQL (PostgresSaver) in production when available;
falls back to in-memory (MemorySaver) for offline tests and development.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from incident_swarm.config import Settings, get_settings

log = logging.getLogger("incident_swarm.app.checkpoint")


def get_checkpointer(settings: Settings | None = None) -> BaseCheckpointSaver:
    """Return a persistent PostgresSaver if database is reachable, else MemorySaver."""
    app_settings = settings or get_settings()

    if app_settings.database_url:
        try:
            import psycopg

            # Probe connectivity with 1-second timeout
            with psycopg.connect(app_settings.database_url, connect_timeout=1) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")

            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                conninfo=app_settings.database_url,
                max_size=10,
                kwargs={"autocommit": True},
            )
            checkpointer = PostgresSaver(pool)
            checkpointer.setup()
            log.info("Initialized PostgreSQL checkpointer for episodic memory.")
            return checkpointer
        except Exception:  # noqa: BLE001
            log.info("Postgres unreachable; using MemorySaver checkpoint fallback.")

    return MemorySaver()


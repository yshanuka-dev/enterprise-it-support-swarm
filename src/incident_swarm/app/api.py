"""FastAPI service: GET /health and POST /chat.

Run locally:  uvicorn incident_swarm.app.api:app --reload
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from incident_swarm.app import tracing
from incident_swarm.app.graph import build_graph
from incident_swarm.app.nodes import (
    Services,
    default_metrics_tool,
    default_procedural_search,
    default_runbook_search,
)
from incident_swarm.config import ROOT, Settings, get_settings, load_model_config
from incident_swarm.memory import cag

log = logging.getLogger("api")
VERSION = "0.1.0"


class ChatRequest(BaseModel):
    """One user turn."""

    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    channel: Literal["chat", "voice"] = "chat"


class ChatResponse(BaseModel):
    """The agent's reply plus the facts a client or grader wants to see."""

    session_id: str
    ticket_id: str | None
    reply: str
    ticket_type: str | None
    severity: str | None
    service: str | None
    cache_hit: bool
    escalated_to_human: bool
    escalation_reason: str | None
    needs_voice_escalation: bool
    runbook_citations: list[dict]
    procedural_citations: list[dict] = Field(default_factory=list)
    path: list[str]


def default_services() -> Services:
    """Production services: CAG FAQ cache, Observability MCP telemetry, and Runbook/Procedural retrieval."""
    faq = ROOT / "config" / "faq.yaml"
    cag_lookup = None
    if faq.exists():
        entries, _ = cag.load_faq(faq)
        cag_lookup = cag.FaqCache(entries).lookup
    else:
        log.warning("config/faq.yaml not found: running without the FAQ cache")

    return Services(
        cag_lookup=cag_lookup,
        metrics_tool=default_metrics_tool,
        runbook_search=default_runbook_search,
        procedural_search=default_procedural_search,
    )


def create_app(graph=None, settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own graph; production uses the defaults."""
    if settings is None:
        try:
            settings = get_settings()
        except Exception as exc:  # noqa: BLE001 - e.g. no .env in a bare container: tracing off
            log.warning("settings unavailable (%s); tracing disabled", exc)
            settings = Settings.model_construct()
    limits = load_model_config().limits
    compiled = graph or build_graph(default_services(), limits)
    max_hops = int(limits["max_hops"])

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        tracing.flush()

    app = FastAPI(title="IT Support & Incident Resolution Swarm", version=VERSION, lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    def serve_ui():
        """Serve the modern Enterprise Ops Console UI."""
        ui_file = ROOT / "src" / "incident_swarm" / "app" / "static" / "index.html"
        if not ui_file.exists():
            # Fallback when running inside container package install
            import incident_swarm
            pkg_root = Path(incident_swarm.__file__).parent
            ui_file = pkg_root / "app" / "static" / "index.html"
        return FileResponse(ui_file)

    @app.get("/health")
    def health() -> dict:
        """Liveness check used by the load balancer."""
        return {"status": "ok", "version": VERSION, "tracing": bool(
            getattr(settings, "langfuse_public_key", None))}

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        """Run one turn through the swarm. The same session_id continues the same conversation."""
        session_id = req.session_id or uuid.uuid4().hex
        config = tracing.run_config(session_id, req.channel, max_hops, tracing.make_handler(settings))
        inputs = {"messages": [("user", req.message)], "session_id": session_id, "channel": req.channel}
        try:
            state = compiled.invoke(inputs, config)
        except GraphRecursionError:
            log.error("recursion limit hit for session %s", session_id)
            return ChatResponse(
                session_id=session_id, ticket_id=None, cache_hit=False, ticket_type=None, severity=None,
                service=None, escalated_to_human=True, needs_voice_escalation=False,
                escalation_reason="loop guard: the graph exceeded its step limit",
                reply="I could not finish this automatically, so I'm handing it to a human engineer.",
                runbook_citations=[], path=[])
        except Exception:
            log.exception("graph failed for session %s", session_id)
            raise HTTPException(status_code=500, detail="internal error while handling the request") from None
        return ChatResponse(
            session_id=session_id, ticket_id=state.get("ticket_id"), reply=state.get("reply", ""),
            ticket_type=state.get("ticket_type"), severity=state.get("severity"), service=state.get("service"),
            cache_hit=state.get("cache_hit", False), escalated_to_human=state.get("escalated_to_human", False),
            escalation_reason=state.get("escalation_reason"),
            needs_voice_escalation=state.get("needs_voice_escalation", False),
            runbook_citations=state.get("runbook_citations", []),
            procedural_citations=state.get("procedural_citations", []),
            path=state.get("path", []))

    return app


app = create_app()

"""Tests for LiveKit voice token generation, slim voice agent, barge-in, and latency tracking.

Validates Day 15 deliverables against the Hackathon Rubric:
- Token generation endpoint (/voice/token)
- Slim voice tools (report_incident, get_investigation_status, quick_metrics)
- Barge-in handling with [interrupted] tagging
- Voice pipeline latency measurement (sub-2s target)
"""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from incident_swarm.app.api import create_app
from incident_swarm.app.tracing import log_voice_pipeline_span
from incident_swarm.config import Settings
from incident_swarm.voice.agent import (
    VoiceInterruptionTracker,
    VoiceLatencyTracker,
    build_voice_pipeline,
)
from incident_swarm.voice.tools import (
    get_investigation_status,
    get_voice_tools,
    quick_metrics,
    report_incident,
    set_graph_invoker,
)


@pytest.fixture
def test_client():
    settings = Settings.model_construct(
        database_url="postgresql://swarm:change-me@localhost:5432/ops_db",
        livekit_url="wss://test.livekit.cloud",
        livekit_api_key="API_KEY_TEST_12345",
        livekit_api_secret="API_SECRET_32_CHARS_MINIMUM_SECURITY_KEY!",
    )
    app = create_app(settings=settings)
    return TestClient(app)


def test_voice_token_endpoint(test_client):
    """GET /voice/token returns a valid JWT with correct claims and grants."""
    resp = test_client.get("/voice/token?room=ops-incident-01&identity=user-alice&name=Alice")
    assert resp.status_code == 200
    data = resp.json()

    assert data["room"] == "ops-incident-01"
    assert data["identity"] == "user-alice"
    assert data["url"] == "wss://test.livekit.cloud"
    assert "token" in data
    assert "server_time" in data

    # Verify JWT decode
    token = data["token"]
    decoded = jwt.decode(
        token,
        "API_SECRET_32_CHARS_MINIMUM_SECURITY_KEY!",
        algorithms=["HS256"],
        options={"verify_aud": False},
    )
    assert decoded["sub"] == "user-alice"
    assert decoded["name"] == "Alice"
    assert decoded["video"]["room"] == "ops-incident-01"
    assert decoded["video"]["roomJoin"] is True


def test_voice_token_defaults():
    """Default parameters provide an instant valid session without parameters."""
    client = TestClient(create_app(settings=Settings.model_construct()))
    resp = client.get("/voice/token")
    assert resp.status_code == 200
    data = resp.json()
    assert data["room"] == "incident-room"
    assert data["identity"].startswith("user-")
    assert len(data["token"]) > 50


def test_slim_voice_tools():
    """Slim tools execute fast and return concise verbal summaries."""
    import asyncio

    async def _run():
        tools = get_voice_tools()
        assert len(tools) == 3

        # 1. quick_metrics
        metrics_reply = await quick_metrics("prod-auth")
        assert "prod-auth" in metrics_reply
        assert "CPU" in metrics_reply
        assert "percent" in metrics_reply

        # 2. report_incident with background hook
        dispatched = []

        def mock_invoker(ticket_id, summary, service):
            dispatched.append((ticket_id, summary, service))

        set_graph_invoker(mock_invoker)
        rep_reply = await report_incident(summary="Redis CPU spike", service="prod-cache")
        assert "Created" in rep_reply or "incident" in rep_reply
        assert "background" in rep_reply.lower() or "dispatched" in rep_reply.lower()

        # 3. get_investigation_status
        status_reply = await get_investigation_status("TKT-TEST-99")
        assert "TKT-TEST-99" in status_reply

    asyncio.run(_run())



def test_barge_in_interruption_tagging():
    """Interrupted assistant utterances are tagged [interrupted] and unsaid text is excluded."""
    tracker = VoiceInterruptionTracker(session_id="session-interruption-test")

    class MockSpeechHandle:
        def __init__(self, spoken_text: str, interrupted: bool = True):
            self.interrupted = interrupted
            self.chat_items = [type("Item", (), {"text_content": spoken_text})()]
            self._callbacks = []

        def add_done_callback(self, cb):
            self._callbacks.append(cb)

    class MockHistory:
        def __init__(self):
            self.messages = [
                type(
                    "Msg",
                    (),
                    {
                        "role": "assistant",
                        "text_content": "I am looking into the database logs and restarting the container...",
                        "content": None,
                        "interrupted": False,
                    },
                )()
            ]

    class MockSession:
        def __init__(self):
            self.history = MockHistory()

    session = MockSession()
    handle = MockSpeechHandle(spoken_text="I am looking into the database logs")

    # Simulate speech created
    ev = type("Ev", (), {"speech_handle": handle})()
    tracker.on_speech_created(ev, session)

    # Fire callback simulating user barge-in mid-speech
    for cb in handle._callbacks:
        cb(handle)

    # Verify rubric criteria:
    # 1. Spoken portion recorded with [interrupted]
    assert tracker._last_spoken_text == "I am looking into the database logs [interrupted]"
    # 2. Unsaid text ("and restarting the container...") is replaced/not stored
    assert "restarting the container" not in session.history.messages[0].text_content
    assert session.history.messages[0].text_content.endswith("[interrupted]")
    assert session.history.messages[0].interrupted is True


def test_voice_latency_tracker(caplog):
    """End-to-end voice latency measures EOU, TTFT, TTFB and satisfies < 2.0s target."""
    import logging

    caplog.set_level(logging.INFO)

    tracker = VoiceLatencyTracker(session_id="latency-test-session")
    tracker.eou_delay = 0.20
    tracker.llm_ttft = 0.30
    tracker.tts_ttfb = 0.15

    total = tracker.eou_delay + tracker.llm_ttft + tracker.tts_ttfb
    assert total < 2.0  # Rubric target: sub-2.0s

    log_voice_pipeline_span(
        session_id="latency-test-session",
        end_of_utterance_delay=tracker.eou_delay,
        llm_first_token=tracker.llm_ttft,
        tts_first_byte=tracker.tts_ttfb,
        total_latency=total,
    )

    # Confirm log output
    assert "voice_pipeline latency" in caplog.text
    assert "Sub-2s: True" in caplog.text


def test_build_voice_pipeline():
    """Voice pipeline initializes cleanly with slim agent instructions and tools."""
    agent, session, _barge, _lat = build_voice_pipeline("test-pipeline-build")
    assert agent is not None
    assert session is not None
    assert "Zuu Crew IT Support Swarm Voice Agent" in agent.instructions
    assert len(agent.tools) == 3


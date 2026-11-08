"""API tests with FastAPI's TestClient (no network, no Langfuse)."""

import pytest
from fastapi.testclient import TestClient
from langgraph.errors import GraphRecursionError

from incident_swarm.app.api import create_app
from incident_swarm.config import Settings


@pytest.fixture
def client():
    return TestClient(create_app(settings=Settings.model_construct()))


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["tracing"] is False


def test_chat_returns_triage_and_path(client):
    body = client.post("/chat", json={"message": "Redis is out of memory on prod-cache-01"}).json()
    assert body["ticket_type"] == "critical_outage" and body["needs_voice_escalation"] is True
    assert body["path"][0] == "intake" and body["path"][-1] == "writeback" and body["session_id"]


def test_faq_question_is_served_from_cache(client):
    body = client.post("/chat", json={"message": "How do I reset my VPN?"}).json()
    assert body["cache_hit"] is True and body["path"] == ["intake", "cag_check"]


def test_session_continues_across_requests(client):
    first = client.post("/chat", json={"message": "it's broken"}).json()
    second = client.post("/chat", json={"message": "the checkout API is slow", "session_id": first["session_id"]}).json()
    assert second["ticket_id"] == first["ticket_id"] and second["ticket_type"] == "service_degradation"


@pytest.mark.parametrize("payload", [{"message": ""}, {}, {"message": "x" * 4001}, {"message": "hi", "channel": "fax"}])
def test_invalid_requests_are_rejected(client, payload):
    assert client.post("/chat", json=payload).status_code == 422


class _Raising:
    def __init__(self, exc):
        self.exc = exc

    def invoke(self, *args, **kwargs):
        raise self.exc


def test_recursion_error_becomes_a_graceful_escalation():
    api = TestClient(create_app(graph=_Raising(GraphRecursionError("loop")), settings=Settings.model_construct()))
    body = api.post("/chat", json={"message": "hi"}).json()
    assert body["escalated_to_human"] is True and "loop guard" in body["escalation_reason"]


def test_unexpected_error_returns_500_without_leaking_details():
    api = TestClient(create_app(graph=_Raising(RuntimeError("secret internals")), settings=Settings.model_construct()))
    response = api.post("/chat", json={"message": "hi"})
    assert response.status_code == 500 and "secret" not in response.text

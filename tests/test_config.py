"""Config and fixture sanity tests."""

import json
from pathlib import Path

import pytest

from incident_swarm.config import get_settings, load_model_config

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
REQUIRED_KEYS = {"transcript_id", "ticket_type", "channel", "messages", "resolution_time_minutes"}


def test_every_role_maps_to_a_known_tier():
    cfg = load_model_config()
    for role in cfg.roles:
        assert cfg.tier_for(role).model


def test_limits_match_rubric():
    limits = load_model_config().limits
    assert limits["max_tool_retries"] == 3
    assert limits["voice_target_latency_s"] <= 2.0


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    get_settings.cache_clear()
    assert get_settings().database_url.endswith("/db")
    get_settings.cache_clear()


def test_sample_transcripts_have_expected_shape():
    files = list(FIXTURES.glob("*.json"))
    if not files:
        pytest.skip("copy a few sample transcripts into tests/fixtures/transcripts/")
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert REQUIRED_KEYS <= doc.keys(), path.name
        assert doc["messages"], path.name

"""Distillation tests: preprocessing, prompt building and the LLM call (with a fake client)."""

import pytest
from pydantic import ValidationError

from incident_swarm.pipeline import distill

DOC = {
    "transcript_id": "TXN-1",
    "ticket_type": "asset_provisioning",
    "channel": "chat",
    "messages": [
        {"role": "user", "text": "My laptop screen has dead pixels."},
        {"role": "agent", "text": "Checking our systems now."},
        {"role": "agent", "text": "Checking  our systems now."},
        {"role": "user", "text": "My laptop screen has dead pixels."},
        {"role": "agent", "text": "I diagnosed the dock: firmware 1.2.3 is below 1.4.0. I flashed 1.4.0."},
    ],
}


def _result(**over):
    base = dict(
        user_problem="Dead pixels", has_concrete_resolution=True, resolution_addresses_problem=False,
        resolved_problem="Dock USB-C failure", root_cause="Old dock firmware",
        root_cause_category="software_bug", resolution_steps=["Flashed firmware 1.4.0"],
        affected_service=None, confidence=0.9,
    )
    return distill.Distilled(**{**base, **over})


class FakeClient:
    """Mimics client.models.generate_content and records the call."""

    def __init__(self, parsed):
        self.calls = []
        self.models = self
        self._parsed = parsed

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return type("Resp", (), {"parsed": self._parsed, "text": ""})()


def test_clean_messages_drops_repeats_and_normalises_whitespace():
    cleaned = distill.clean_messages(DOC["messages"])
    assert len(cleaned) == 3
    assert [m["role"] for m in cleaned] == ["user", "agent", "agent"]


def test_resolution_is_longest_agent_message():
    cleaned = distill.clean_messages(DOC["messages"])
    assert "dock" in cleaned[distill.find_resolution_index(cleaned)]["text"]


def test_find_resolution_returns_none_without_agent_turns():
    assert distill.find_resolution_index([{"role": "user", "text": "hi"}]) is None


def test_prompt_contains_metadata_hint_and_no_duplicates():
    prompt = distill.build_prompt(DOC)
    assert "TXN-1" in prompt and "Hint:" in prompt
    assert prompt.count("dead pixels") == 1


def test_distill_one_passes_system_prompt_and_returns_parsed():
    client = FakeClient(_result())
    out = distill.distill_one(client, "some-model", DOC)
    assert out.resolved_problem == "Dock USB-C failure"
    call = client.calls[0]
    assert call["model"] == "some-model"
    assert call["config"].system_instruction == distill.SYSTEM_PROMPT


def test_confidence_is_clamped_and_category_validated():
    assert _result(confidence=1.7).confidence == 1.0
    with pytest.raises(ValidationError):
        _result(root_cause_category="made_up")


def test_make_record_keeps_metadata():
    rec = distill.make_record("transcripts/TXN-1.json", DOC, _result())
    assert rec["transcript_id"] == "TXN-1" and rec["ticket_type"] == "asset_provisioning"
    assert rec["resolution_addresses_problem"] is False


def test_resume_skips_done_keys_and_logs_failures(tmp_path, monkeypatch):
    import json

    import boto3
    from moto import mock_aws

    from incident_swarm.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    get_settings.cache_clear()
    fake = FakeClient(_result())
    monkeypatch.setattr(distill.genai, "Client", lambda api_key: fake)
    out = tmp_path / "out.jsonl"
    with mock_aws():
        s3 = boto3.client("s3", region_name=get_settings().aws_region)
        s3.create_bucket(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": get_settings().aws_region},
        )
        for i in range(3):
            doc = {**DOC, "transcript_id": f"TXN-{i}"}
            s3.put_object(Bucket="test-bucket", Key=f"transcripts/TXN-{i}.json", Body=json.dumps(doc))
        argv = ["--limit", "0", "--bucket", "test-bucket", "--out", str(out)]
        assert distill.main(argv) == 0
        assert len(out.read_text().splitlines()) == 3
        calls_before = len(fake.calls)
        assert distill.main(argv) == 0
        assert len(fake.calls) == calls_before  # nothing re-processed
        assert len(out.read_text().splitlines()) == 3
    get_settings.cache_clear()
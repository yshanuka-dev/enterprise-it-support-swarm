"""Ingest tests: retry classification, concurrent resumable distillation (mocked S3), stats."""

import json

import boto3
import pytest
from moto import mock_aws

from incident_swarm.config import get_settings
from incident_swarm.pipeline import distill, ingest

DOC = {
    "transcript_id": "TXN-1", "ticket_type": "critical_outage", "channel": "chat",
    "messages": [{"role": "user", "text": "Redis is down"}, {"role": "agent", "text": "Fixed it."}],
}


class RateLimitError(Exception):
    status_code = 429


def _distilled():
    return distill.Distilled(
        user_problem="u", has_concrete_resolution=True, resolved_problem="r",
        match_reasoning="same", resolution_addresses_problem=True, root_cause="c",
        root_cause_category="software_bug", resolution_steps=["s"], affected_service=None, confidence=0.9,
    )


def test_is_retryable_classification():
    assert ingest.is_retryable(RateLimitError("slow down"))
    assert ingest.is_retryable(TimeoutError("t"))  # class name contains 'timeout'
    assert not ingest.is_retryable(ValueError("bad json"))


def test_with_retries_recovers_then_gives_up():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("429")
        return "ok"

    assert ingest.with_retries(flaky, sleep=lambda s: None) == "ok" and calls["n"] == 3
    with pytest.raises(RateLimitError):
        ingest.with_retries(lambda: (_ for _ in ()).throw(RateLimitError("x")), attempts=2, sleep=lambda s: None)
    with pytest.raises(ValueError):  # non-retryable: raised immediately
        ingest.with_retries(lambda: (_ for _ in ()).throw(ValueError("x")), sleep=lambda s: None)


@pytest.fixture
def bucket(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    get_settings.cache_clear()
    with mock_aws():
        s3 = boto3.client("s3", region_name=get_settings().aws_region)
        s3.create_bucket(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": get_settings().aws_region},
        )
        for i in range(5):
            s3.put_object(Bucket="test-bucket", Key=f"transcripts/TXN-{i}.json",
                          Body=json.dumps({**DOC, "transcript_id": f"TXN-{i}"}))
        yield s3
    get_settings.cache_clear()


def test_distill_all_retries_logs_failures_and_resumes(bucket, tmp_path):
    out = tmp_path / "all.jsonl"
    attempts: dict[str, int] = {}

    def fake(doc):
        tid = doc["transcript_id"]
        attempts[tid] = attempts.get(tid, 0) + 1
        if tid == "TXN-1" and attempts[tid] < 3:
            raise RateLimitError("429")  # recovers after retries
        if tid == "TXN-3":
            raise ValueError("model returned garbage")  # permanent failure
        return _distilled()

    ok, failed = ingest.distill_all(bucket, "test-bucket", "transcripts/", out, 3, 0, fake, sleep=lambda s: None)
    assert (ok, failed) == (4, 1)
    assert len(out.read_text().splitlines()) == 4
    assert "TXN-3" in out.with_suffix(".errors.jsonl").read_text()

    seen_before = dict(attempts)
    ok, failed = ingest.distill_all(bucket, "test-bucket", "transcripts/", out, 3, 0, fake, sleep=lambda s: None)
    assert (ok, failed) == (0, 1)  # only the failed one is retried
    assert attempts["TXN-0"] == seen_before["TXN-0"]


def test_index_records_counts_and_stats(capsys):
    from qdrant_client import QdrantClient

    try:
        from tests.test_procedural import FakeEmbedder, _record
    except ModuleNotFoundError:
        from test_procedural import FakeEmbedder, _record

    records = [_record("T1", "a b", "c"), _record("T2", "d e", "f", conf=0.2)]
    stats = ingest.index_records(records, QdrantClient(":memory:"), FakeEmbedder(), 0.6)
    assert stats == {"indexed": 1, "skipped": 1}
    ingest.print_stats(records, 0.6)
    assert "would be indexed" in capsys.readouterr().out

def test_rate_limiter_spaces_calls():
    now = [0.0]
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    limiter = ingest.RateLimiter(60, clock=lambda: now[0], sleep=fake_sleep)  # 1 call per second
    for _ in range(3):
        limiter.wait()
    assert sleeps == [1.0, 1.0]
    assert ingest.RateLimiter(0).interval == 0.0


def test_circuit_breaker_stops_run_after_consecutive_failures(bucket, tmp_path):
    out = tmp_path / "all.jsonl"

    def always_blocked(doc):
        raise PermissionError("403 blocked")

    ok, failed = ingest.distill_all(bucket, "test-bucket", "transcripts/", out, 1, 0, always_blocked,
                                    sleep=lambda s: None, max_consecutive_failures=2)
    assert ok == 0 and failed == 2  # 5 transcripts exist, but the run stopped early

"""S3 upload tests using a mocked S3 (no AWS account needed)."""

import json

import boto3
import pytest
from moto import mock_aws

from incident_swarm.config import get_settings
from incident_swarm.pipeline import s3_upload


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_files(folder, n_valid=3):
    for i in range(n_valid):
        doc = {"transcript_id": f"TXN-{i:05d}", "messages": [{"role": "user", "text": "hi"}]}
        (folder / f"TXN-{i:05d}.json").write_text(json.dumps(doc), encoding="utf-8")
    (folder / "broken.json").write_text("{not json", encoding="utf-8")


@mock_aws
def test_uploads_valid_files_and_skips_invalid(tmp_path):
    _make_files(tmp_path)
    argv = ["--source", str(tmp_path), "--bucket", "test-bucket", "--create-bucket", "--expected", "3"]
    assert s3_upload.main(argv) == 0
    s3 = boto3.client("s3", region_name=get_settings().aws_region)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="test-bucket")["Contents"]]
    assert len(keys) == 3 and all(k.startswith("transcripts/") for k in keys)


@mock_aws
def test_second_run_is_idempotent(tmp_path, caplog):
    _make_files(tmp_path)
    argv = ["--source", str(tmp_path), "--bucket", "test-bucket", "--create-bucket", "--expected", "3"]
    assert s3_upload.main(argv) == 0
    caplog.clear()
    with caplog.at_level("INFO"):
        assert s3_upload.main(argv) == 0
    assert "3 already uploaded, 0 to upload" in caplog.text


@mock_aws
def test_count_mismatch_fails(tmp_path):
    _make_files(tmp_path)
    argv = ["--source", str(tmp_path), "--bucket", "test-bucket", "--create-bucket", "--expected", "1050"]
    assert s3_upload.main(argv) == 1


def test_dry_run_needs_no_aws(tmp_path):
    _make_files(tmp_path)
    assert s3_upload.main(["--source", str(tmp_path), "--bucket", "x", "--dry-run", "--expected", "0"]) == 0
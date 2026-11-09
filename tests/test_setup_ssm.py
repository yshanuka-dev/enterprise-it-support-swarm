"""Unit tests for AWS SSM parameter sync (using moto)."""

from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from scripts import setup_ssm


@pytest.fixture
def mock_ssm(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    with mock_aws():
        client = boto3.client("ssm", region_name="ap-southeast-1")
        yield client


def test_load_env_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n"
        "DATABASE_URL=postgresql://u:p@localhost:5432/db\n"
        "GOOGLE_API_KEY='secret-key'\n"
        "EMPTY_VAL=\n"
        "INVALID LINE WITHOUT EQUALS\n",
        encoding="utf-8",
    )
    pairs = setup_ssm.load_env_file(env_file)
    assert pairs["DATABASE_URL"] == "postgresql://u:p@localhost:5432/db"
    assert pairs["GOOGLE_API_KEY"] == "secret-key"
    assert pairs["EMPTY_VAL"] == ""


def test_sync_and_verify_ssm_parameters(mock_ssm):
    env_data = {
        "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
        "GOOGLE_API_KEY": "test-key-12345",
        "QDRANT_URL": "",  # Empty -> should skip
    }

    # Dry-run
    synced, skipped = setup_ssm.sync_ssm_parameters(mock_ssm, env_data, prefix="/test-swarm", dry_run=True)
    assert synced == 2
    assert skipped == 9  # remaining keys empty

    # Real sync
    synced, skipped = setup_ssm.sync_ssm_parameters(mock_ssm, env_data, prefix="/test-swarm", dry_run=False)
    assert synced == 2
    assert skipped == 9

    # Verify status
    status = setup_ssm.verify_ssm_parameters(mock_ssm, prefix="/test-swarm")
    assert status["DATABASE_URL"] is True
    assert status["GOOGLE_API_KEY"] is True
    assert status["QDRANT_URL"] is False

    # Check parameter value in SSM
    res = mock_ssm.get_parameter(Name="/test-swarm/DATABASE_URL", WithDecryption=True)
    assert res["Parameter"]["Value"] == "postgresql://u:p@localhost:5432/db"
    assert res["Parameter"]["Type"] == "SecureString"


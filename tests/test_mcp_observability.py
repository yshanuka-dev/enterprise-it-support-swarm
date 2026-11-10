"""Unit tests for Observability MCP Server tools."""

from __future__ import annotations

import pytest

from incident_swarm.mcp_servers.db import DatabaseManager
from incident_swarm.mcp_servers.observability import (
    get_metrics,
    get_service_info,
    list_anomalies,
)


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure each test runs with a fresh database manager instance."""
    import incident_swarm.mcp_servers.db as db_mod

    db_mod._db_manager = DatabaseManager(dsn=None)  # Force in-memory mode for tests
    yield
    db_mod._db_manager = None


class TestObservabilityMCP:
    def test_get_metrics_healthy_service(self):
        res = get_metrics("auth-api", window_minutes=30)
        assert res["status"] == "success"
        assert res["service"] == "auth-api"
        assert res["window_minutes"] == 30
        summary = res["summary"]
        assert summary["current_status"] == "healthy"
        assert summary["avg_cpu_percent"] < 50.0
        assert summary["avg_error_rate_percent"] < 1.0

    def test_get_metrics_degraded_service(self):
        res = get_metrics("redis-cluster", window_minutes=60)
        assert res["status"] == "success"
        summary = res["summary"]
        assert summary["current_status"] == "degraded"
        assert summary["max_memory_percent"] > 90.0
        assert summary["max_cpu_percent"] > 80.0
        assert summary["avg_p95_latency_ms"] > 500.0

    def test_get_metrics_unknown_service_fallback(self):
        res = get_metrics("custom-worker-service")
        assert res["status"] == "success"
        assert res["summary"]["current_status"] == "healthy"
        assert res["sample_count"] >= 1

    def test_list_anomalies_all_services(self):
        res = list_anomalies()
        assert res["status"] == "success"
        assert res["anomaly_count"] >= 1
        anomalies = res["anomalies"]
        service_names = [a["service"] for a in anomalies]
        assert "redis-cluster" in service_names

        redis_anomaly = next(a for a in anomalies if a["service"] == "redis-cluster")
        assert any("Memory exhaustion" in r for r in redis_anomaly["reasons"])

    def test_list_anomalies_filtered_service(self):
        res = list_anomalies(service="auth-api")
        assert res["status"] == "success"
        assert res["anomaly_count"] == 0

    def test_get_service_info_existing(self):
        res = get_service_info("auth-api")
        assert res["status"] == "success"
        info = res["service_info"]
        assert info["id"] == "SVC-001"
        assert info["name"] == "auth-api"
        assert info["owner_division"] == "DIV-001"
        assert info["status"] == "healthy"
        assert info["version"] == "v2.2.15"

    def test_get_service_info_by_id(self):
        res = get_service_info("SVC-007")
        assert res["status"] == "success"
        assert res["service_info"]["name"] == "redis-cluster"

    def test_get_service_info_not_found(self):
        res = get_service_info("unknown-ghost-service")
        assert res["status"] == "failure"
        assert "error" in res


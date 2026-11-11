"""Unit tests for L1 Triage: structured classification, regex rules, and entity extraction."""

from incident_swarm.app.triage import (
    TriageResult,
    classify_by_rules,
    extract_entities,
    triage_message,
)


def test_triage_result_schema():
    """Verify TriageResult pydantic model serialization and constraints."""
    res = TriageResult(
        ticket_type="critical_outage",
        severity="critical",
        service="redis-cluster",
        confidence=0.95,
        reasoning="OOM kill signal observed",
        user_id="EMP-0012",
        asset_id="AST-0014",
    )
    data = res.model_dump()
    assert data["ticket_type"] == "critical_outage"
    assert data["severity"] == "critical"
    assert data["user_id"] == "EMP-0012"
    assert data["asset_id"] == "AST-0014"
    assert 0.0 <= data["confidence"] <= 1.0


def test_extract_entities():
    """Verify extraction of employee IDs, hardware asset tags, and service names."""
    text1 = "EMP-0044 reported that AST-0003 has a swollen battery on prod-cache-01"
    e1 = extract_entities(text1)
    assert e1["user_id"] == "EMP-0044"
    assert e1["asset_id"] == "AST-0003"
    assert e1["service"] == "prod-cache-01"

    text2 = "Need password reset for EMP-0001 on auth-api"
    e2 = extract_entities(text2)
    assert e2["user_id"] == "EMP-0001"
    assert e2["asset_id"] is None
    assert e2["service"] == "auth-api"


def test_classify_by_rules_categories():
    """Verify deterministic regex rules across IT domains."""
    # Critical outage
    outage = classify_by_rules("Postgres primary crashed and 503 errors on api")
    assert outage.ticket_type == "critical_outage"
    assert outage.severity == "critical"
    assert outage.confidence >= 0.8

    # Service degradation
    degradation = classify_by_rules("Checkout service is experiencing extreme latency and timeouts")
    assert degradation.ticket_type == "service_degradation"
    assert degradation.severity == "medium"

    # Asset provisioning
    asset = classify_by_rules("My laptop monitor screen is flickering")
    assert asset.ticket_type == "asset_provisioning"
    assert asset.severity == "low"

    # Access and identity
    access = classify_by_rules("Unable to log in to VPN with my authenticator MFA")
    assert access.ticket_type == "access_identity"
    assert access.severity == "low"

    # Ambiguous input
    ambiguous = classify_by_rules("it is broken")
    assert ambiguous.ticket_type == "unknown"
    assert ambiguous.confidence < 0.5


def test_triage_message_end_to_end():
    """Verify triage_message combines entity extraction and classification."""
    msg = "EMP-0089: My laptop screen has dead pixels on AST-0014"
    result = triage_message(msg)
    assert result.ticket_type == "asset_provisioning"
    assert result.user_id == "EMP-0089"
    assert result.asset_id == "AST-0014"

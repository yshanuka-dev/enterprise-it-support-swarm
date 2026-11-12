"""L1 Triage module with structured output schema and deterministic fallback.

Classifies incoming user messages into:
- ticket_type: access_identity, asset_provisioning, service_degradation, critical_outage, unknown
- severity: low, medium, critical, unknown
- service: affected service or host name (e.g. 'redis-cluster', 'prod-cache-01')
- confidence: 0.0 to 1.0 (scores below 0.5 route to clarification)
- user_id: extracted employee ID (e.g. 'EMP-0012')
- asset_id: extracted hardware asset ID (e.g. 'AST-0014')
"""

from __future__ import annotations

import logging
import os
import re

from pydantic import BaseModel, Field

from incident_swarm.app.state import Severity, TicketType
from incident_swarm.config import Settings, get_settings, load_model_config

log = logging.getLogger("incident_swarm.app.triage")

# Entity extraction patterns
EMP_PATTERN = re.compile(r"\bEMP-\d{4}\b", re.IGNORECASE)
AST_PATTERN = re.compile(r"\b(?:AST-\d{4}|(?:LP|SW|LB|NAS)-[A-Za-z0-9\-]+)\b", re.IGNORECASE)
HOST_PATTERN = re.compile(r"\b(?:prod|staging)-[a-z]+-\d+\b", re.IGNORECASE)
SERVICE_WORDS = re.compile(
    r"\b(redis(?:-cluster)?|postgres(?:-primary|-replica)?|auth(?:-api)?|checkout(?:-api)?|"
    r"user-service|notification-service|search-service|vpn(?:-service)?|rabbitmq|"
    r"elasticsearch|kibana|jenkins|grafana|prometheus|cdn-service)\b",
    re.IGNORECASE,
)

# Rule-based triage patterns (deterministic fallback)
TRIAGE_RULES: tuple[tuple[TicketType, Severity, re.Pattern], ...] = (
    ("critical_outage", "critical", re.compile(
        r"\b(out of memory|oom|outage|is down|are down|502|503|crash(?:ed|ing)?|unresponsive|"
        r"not responding|failover|under attack|ddos)\b", re.IGNORECASE)),
    ("service_degradation", "medium", re.compile(
        r"\b(slow|latency|lag|degrad\w*|timeouts?|timing out|delayed|intermittent|high cpu|"
        r"connection pool|pool exhaustion)\b", re.IGNORECASE)),
    ("asset_provisioning", "low", re.compile(
        r"\b(laptop|monitor|keyboard|mouse|dock(?:ing)?|screen|headset|hardware|replacement|"
        r"refresh|ssd|ram|macbook|thinkpad|surface)\b", re.IGNORECASE)),
    ("access_identity", "low", re.compile(
        r"\b(vpn|password|log ?in|locked out|mfa|2fa|authenticator|access|permissions?|ssh|"
        r"sso|account|credential|keycloak|active directory)\b", re.IGNORECASE)),
)


class TriageResult(BaseModel):
    """Structured classification output produced by L1 Triage."""

    ticket_type: TicketType = Field(description="Category of the IT issue")
    severity: Severity = Field(description="Urgency/impact level")
    service: str | None = Field(default=None, description="Affected infrastructure service or host")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence in classification")
    reasoning: str = Field(default="", description="Brief justification for the classification")
    user_id: str | None = Field(default=None, description="Employee ID of the requester if mentioned")
    asset_id: str | None = Field(default=None, description="Hardware asset tag if mentioned")


def extract_entities(text: str) -> dict[str, str | None]:
    """Extract employee ID, hardware asset tag, and service/host from message text."""
    emp = EMP_PATTERN.search(text)
    user_id = emp.group(0).upper() if emp else None

    ast = AST_PATTERN.search(text)
    asset_id = ast.group(0) if ast else None

    host = HOST_PATTERN.search(text)
    if host:
        service = host.group(0).lower()
    else:
        svc = SERVICE_WORDS.search(text)
        service = svc.group(0).lower() if svc else None

    return {"user_id": user_id, "asset_id": asset_id, "service": service}


def classify_by_rules(text: str) -> TriageResult:
    """Fast, deterministic fallback classifier based on keyword regex rules."""
    entities = extract_entities(text)

    # Check for explicit software/restart intent vs hardware
    is_explicit_software = bool(re.search(r"\b(software|reboot|restart|restarting|freeze|crash|bsod)\b", text, re.I))
    has_negated_hardware = bool(re.search(r"\b(not|n't|don't)\s+(?:think\s+)?(?:it's\s+|it\s+is\s+|a\s+)?hardware\b", text, re.I))

    if is_explicit_software or has_negated_hardware:
        return TriageResult(
            ticket_type="service_degradation",
            severity="medium",
            service=entities["service"],
            confidence=0.85,
            reasoning="Rule classifier: Software/OS restart issue identified (hardware negated)",
            user_id=entities["user_id"],
            asset_id=entities["asset_id"],
        )

    matched = [(t, s) for t, s, pattern in TRIAGE_RULES if pattern.search(text)]

    if not matched:
        return TriageResult(
            ticket_type="unknown",
            severity="unknown",
            service=entities["service"],
            confidence=0.2,
            reasoning="Ambiguous input; no domain keywords recognized",
            user_id=entities["user_id"],
            asset_id=entities["asset_id"],
        )

    ticket_type, severity = matched[0]
    confidence = 0.9 if len(matched) == 1 else 0.65

    return TriageResult(
        ticket_type=ticket_type,
        severity=severity,
        service=entities["service"],
        confidence=confidence,
        reasoning=f"Matched keyword pattern for {ticket_type} ({severity})",
        user_id=entities["user_id"],
        asset_id=entities["asset_id"],
    )


def triage_with_llm(text: str, settings: Settings) -> TriageResult | None:
    """Attempt structured classification using the configured small LLM."""
    if not settings.google_api_key and not settings.groq_api_key:
        return None

    try:
        # If Gemini API key is available, call Gemini via google-genai structured JSON
        if settings.google_api_key:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=settings.google_api_key,
                http_options={"api_version": "v1beta", "timeout": 10000},
            )
            model_name = load_model_config().tiers.get("triage", "gemini-2.5-flash")
            prompt = (
                "You are an L1 IT Support Triage agent. Analyze the following user message and "
                "classify ticket_type, severity, affected service, confidence (0.0-1.0), reasoning, "
                "and any mentioned user_id (e.g. EMP-0012) or asset_id (e.g. AST-0014).\n\n"
                "Taxonomy Guidelines:\n"
                "- critical_outage (severity: critical): Production infrastructure/service completely down, crashing, out of memory (OOM), 502/503, unresponsive, database failover.\n"
                "- service_degradation (severity: medium): Slow performance, latency, timeouts, pool exhaustion, intermittent reboots, OS/software crashing on laptops or workstations.\n"
                "- asset_provisioning (severity: low): Physical hardware breakage, damaged laptop screen, broken monitor/keyboard/mouse, equipment ordering/refresh, physical replacement RMA.\n"
                "- access_identity (severity: low): Passwords, VPN, MFA, 2FA, SSO, authentication, accounts, clearance.\n"
                "- unknown (severity: unknown): Ambiguous requests lacking sufficient IT context.\n\n"
                "Important Distinction: Do NOT classify an issue as 'asset_provisioning' if the problem is a software crash, OS freeze, driver reboot, or if the user explicitly says it is NOT a hardware issue. Hardware replacement is strictly for physical equipment damage/procurement.\n\n"
                f"User Message: {text}"
            )
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TriageResult,
                    temperature=0.1,
                ),
            )
            if response.text:
                return TriageResult.model_validate_json(response.text)
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM structured triage failed (%s); attempting Groq fallback.", exc)

    # Secondary fallback to Groq if Gemini hits rate-limits (429)
    if settings.groq_api_key:
        try:
            from groq import Groq
            client = Groq(api_key=settings.groq_api_key)
            prompt = (
                "You are an L1 IT Support Triage agent. Analyze the user message and return a JSON object with: "
                "ticket_type (critical_outage, service_degradation, asset_provisioning, access_identity, unknown), "
                "severity (critical, medium, low, unknown), service (string or null), confidence (0.0-1.0), "
                "reasoning (string), user_id (e.g. EMP-0012 or null), asset_id (e.g. AST-0014 or null).\n"
                "Note: Do not classify software/OS restart/freeze as asset_provisioning.\n\n"
                f"User Message: {text}"
            )
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            content = chat_completion.choices[0].message.content
            if content:
                return TriageResult.model_validate_json(content)
        except Exception as groq_exc:  # noqa: BLE001
            log.warning("Groq structured triage fallback also failed (%s)", groq_exc)

    return None


# Words indicating nuance, negation, or mixed domain that should NOT be short-circuited by regex
NUANCED_INDICATORS = re.compile(
    r"\b(not|n't|don't|doesn't|didn't|won't|software|reboot|restart|crash|freeze|update|patch|"
    r"virus|malware|slow|bug|blue ?screen|bsod|driver|reinstall|corrupt|restarting)\b",
    re.IGNORECASE,
)


def triage_message(text: str, settings: Settings | None = None) -> TriageResult:
    """Classify user turn: uses fast deterministic rules for clear intent, LLM for nuanced turns."""
    rule_res = classify_by_rules(text)

    # 1. Obvious ambiguous/terse input without domain keywords needs no LLM
    if rule_res.ticket_type == "unknown" and len(text.strip().split()) <= 3:
        return rule_res

    # 2. If the message contains negations ("don't", "not") or software/troubleshooting words mixed with hardware,
    # skip the dumb regex short-circuit and let the LLM reason about the real problem.
    is_nuanced = bool(NUANCED_INDICATORS.search(text))

    # 3. Fast deterministic check for unambiguous clear-cut intent (preserves LLM quota)
    # Only fast-path if NOT in an automated test requiring LLM or if the turn is simple & un-nuanced
    if not is_nuanced and rule_res.confidence >= 0.85:
        return rule_res

    # 4. In automated unit test environment (when no keys provided), fallback to rule result
    app_settings = settings or get_settings()
    if os.environ.get("PYTEST_CURRENT_TEST") and not app_settings.google_api_key and not app_settings.groq_api_key:
        return rule_res

    # 5. Nuanced or ambiguous message: invoke small LLM for structured reasoning
    llm_result = triage_with_llm(text, app_settings)
    if llm_result is not None:
        # If the LLM classified ticket_type as unknown, confidence must reflect ambiguity
        if llm_result.ticket_type == "unknown":
            llm_result.confidence = min(llm_result.confidence, 0.3)
        entities = extract_entities(text)
        if not llm_result.user_id and entities["user_id"]:
            llm_result.user_id = entities["user_id"]
        if not llm_result.asset_id and entities["asset_id"]:
            llm_result.asset_id = entities["asset_id"]
        if not llm_result.service and entities["service"]:
            llm_result.service = entities["service"]
        return llm_result

    # 6. Fallback to rule result
    return rule_res

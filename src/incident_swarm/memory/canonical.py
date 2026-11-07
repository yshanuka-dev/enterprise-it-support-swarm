"""Canonical incident ids for distilled records.

The historical data reuses a small set of incidents, each written in several agent styles
(professional, technical, filler-heavy, terse). Grouping them under one canonical id lets search
show each fix once, with a clean title, a sane category and the runbook that covers it.

Rules are checked in order, so more specific incidents must come before general ones
(for example the auth service message mentions Redis, so auth_oom comes first).
"""

from __future__ import annotations

from typing import NamedTuple


class Canonical(NamedTuple):
    """One canonical incident."""

    id: str
    title: str
    category: str
    runbook_id: str | None
    keywords: tuple[str, ...]


RULES: tuple[Canonical, ...] = (
    Canonical("auth_oom", "Auth service pods OOMKilled (memory limit too low)", "application_service",
              "RB-AUTH-OOM", ("auth-api", "oomkilled")),
    Canonical("redis_oom", "Redis out of memory (noeviction policy)", "infrastructure",
              "RB-REDIS-OOM", ("redis",)),
    Canonical("db_connection_leak", "Postgres connections exhausted by a leaking service", "database",
              "RB-PG-CONNECTIONS", ("connection leak", "idle in transaction", "connections waiting",
                                    "pg_stat_activity")),
    Canonical("missing_index", "Slow queries from a missing database index", "database",
              "RB-SLOW-API-INDEX", ("missing index", "create index", "sequential scan",
                                    "full table scan")),
    Canonical("logrotate_gzip", "Log rotation cron spawning parallel gzip processes", "infrastructure",
              None, ("gzip", "runaway cron", "log rotation")),
    Canonical("smtp_pool", "SMTP connection pool exhaustion dropping emails", "application_service",
              None, ("smtp",)),
    Canonical("session_cache_leak", "Memory leak in the user session cache", "application_service",
              None, ("usersessioncache", "memory leak", "hashmap")),
    Canonical("stuck_reindex", "Stuck search reindex job", "application_service",
              None, ("reindex",)),
    Canonical("vpn_stale_session", "VPN blocked by a stale RADIUS session", "access_identity",
              "RB-VPN-SESSION", ("vpn", "radius")),
    Canonical("kerberos_password_sync", "Password change not accepted (stale Kerberos tickets)",
              "access_identity", "RB-ACCOUNT-ACCESS",
              ("kerberos", "password sync", "password synced", "password change")),
    Canonical("staging_db_deny_list", "Staging database access blocked by a deny list", "access_identity",
              "RB-ACCOUNT-ACCESS", ("deny list", "pg_hba", "staging db", "staging database")),
    Canonical("ssh_deploy_access", "Production SSH and deploy access request", "access_identity",
              "RB-ACCOUNT-ACCESS", ("ssh",)),
    Canonical("mfa_desync", "MFA codes rejected (TOTP clock drift)", "access_identity",
              "RB-ACCOUNT-ACCESS", ("totp", "mfa")),
    Canonical("lcd_panel", "Laptop LCD panel failure (dead pixels)", "hardware_asset",
              "RB-HARDWARE", ("lcd", "dead pixel", "screen's dead")),
    Canonical("dock_firmware", "Docking station USB-C failure (old firmware)", "hardware_asset",
              "RB-HARDWARE", ("dock",)),
    Canonical("keyboard_replacement", "Keyboard replacement", "hardware_asset",
              "RB-HARDWARE", ("keyboard",)),
    Canonical("laptop_refresh", "Laptop refresh approval", "hardware_asset",
              "RB-HARDWARE", ("laptop refresh", "refresh request")),
    Canonical("monitor_request", "Monitor request approval", "hardware_asset",
              "RB-HARDWARE", ("u2723qe", "monitor approved", "monitor request", "dell 4k")),
)
BY_ID = {rule.id: rule for rule in RULES}


def canonical_id(resolution_message: str | None) -> str | None:
    """Return the canonical incident id for a resolution message, or None if nothing matches."""
    text = (resolution_message or "").lower()
    for rule in RULES:
        if any(keyword in text for keyword in rule.keywords):
            return rule.id
    return None


def tag_record(record: dict) -> dict:
    """Return a copy of the record with canonical_id, title, category and runbook_id added."""
    cid = canonical_id(record.get("resolution_message"))
    if cid is None:
        return {**record, "canonical_id": None}
    rule = BY_ID[cid]
    return {
        **record,
        "canonical_id": cid,
        "canonical_title": rule.title,
        "canonical_category": rule.category,
        "runbook_id": rule.runbook_id,
    }


def collapse_hits(hits: list[dict], k: int) -> list[dict]:
    """Keep the best hit per canonical incident and count how many near-duplicates were folded in."""
    kept: dict[str, dict] = {}
    for hit in sorted(hits, key=lambda h: -h["score"]):
        key = hit.get("canonical_id") or f"single:{hit.get('transcript_id')}"
        if key in kept:
            kept[key]["similar_cases"] += 1
        else:
            kept[key] = {**hit, "similar_cases": 1}
    return list(kept.values())[:k]

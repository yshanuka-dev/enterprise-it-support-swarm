"""Day 1 sanity check: database seeded correctly, env keys present, model config valid.

Usage: python scripts/check_setup.py
"""

from __future__ import annotations

import sys

import psycopg

from incident_swarm.config import get_settings, load_model_config

EXPECTED_ROWS = {
    "divisions": 8,
    "employees": 750,
    "services": 25,
    "assets_inventory": 200,
    "live_tickets": 150,
    "incident_history": 300,
}


def check_database(url: str) -> bool:
    """Compare table row counts with the seed file and show the DevOps clearance spread."""
    ok = True
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for table, expected in EXPECTED_ROWS.items():
            cur.execute(f"SELECT count(*) FROM {table}")  # table names are constants above
            got = cur.fetchone()[0]
            flag = "ok " if got == expected else "BAD"
            ok &= got == expected
            print(f"  [{flag}] {table:<18} {got:>5} (expected {expected})")
        cur.execute(
            "SELECT clearance_level, count(*) FROM employees "
            "WHERE division_id = 'DIV-002' AND is_active GROUP BY 1 ORDER BY 1"
        )
        print("  active DevOps (DIV-002) by clearance_level:", dict(cur.fetchall()))
    return ok


def main() -> int:
    """Run all checks; return a process exit code."""
    settings = get_settings()
    print("Database:")
    try:
        db_ok = check_database(settings.database_url)
    except psycopg.Error as exc:
        print(f"  cannot connect: {exc}\n  Is `docker compose up -d postgres` running?")
        return 1

    print("Model config:")
    cfg = load_model_config()
    for role, tier in cfg.roles.items():
        print(f"  {role:<18} -> {tier:<7} ({cfg.tiers[tier].model})")

    print("Env keys (set / missing; values never printed):")
    for name in ("anthropic_api_key", "openai_api_key", "langfuse_public_key",
                 "langfuse_secret_key", "qdrant_url", "qdrant_api_key", "s3_bucket",
                 "livekit_url", "livekit_api_key"):
        print(f"  {name:<22} {'set' if getattr(settings, name) else 'missing'}")
    return 0 if db_ok else 1


if __name__ == "__main__":
    sys.exit(main())

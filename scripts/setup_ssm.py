"""Day 7: Provision and sync secrets from .env into AWS SSM Parameter Store as SecureString.

Usage:
    python scripts/setup_ssm.py --dry-run
    python scripts/setup_ssm.py --sync
    python scripts/setup_ssm.py --verify
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from incident_swarm.config import ROOT, get_settings

log = logging.getLogger("setup_ssm")

SSM_PREFIX = "/incident-swarm"

# Secret keys to sync to AWS SSM Parameter Store as SecureString
SECRET_KEYS = (
    "DATABASE_URL",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "QDRANT_URL",
    "QDRANT_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
)


def load_env_file(path: Path) -> dict[str, str]:
    """Parse key=value pairs from a .env file."""
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        pairs[key] = val
    return pairs


def verify_ssm_parameters(ssm_client, prefix: str = SSM_PREFIX) -> dict[str, bool]:
    """Check which parameters exist in SSM under prefix."""
    status = {}
    for key in SECRET_KEYS:
        param_name = f"{prefix}/{key}"
        try:
            ssm_client.get_parameter(Name=param_name, WithDecryption=False)
            status[key] = True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ParameterNotFound":
                status[key] = False
            else:
                log.error("error querying %s: %s", param_name, exc)
                status[key] = False
    return status


def sync_ssm_parameters(ssm_client, values: dict[str, str], prefix: str = SSM_PREFIX,
                        dry_run: bool = False) -> tuple[int, int]:
    """Upload values to SSM Parameter Store as SecureString. Returns (synced, skipped)."""
    synced = skipped = 0
    for key in SECRET_KEYS:
        val = values.get(key)
        param_name = f"{prefix}/{key}"
        if not val:
            log.info("⏭️  Skipping %s (empty in .env)", key)
            skipped += 1
            continue

        masked = val[:4] + "..." + val[-4:] if len(val) > 10 else "***"
        if dry_run:
            log.info("[DRY-RUN] Would put SSM parameter %s = %s (SecureString)", param_name, masked)
            synced += 1
            continue

        try:
            ssm_client.put_parameter(
                Name=param_name,
                Description=f"Incident Resolution Swarm secret: {key}",
                Value=val,
                Type="SecureString",
                Overwrite=True,
                Tier="Standard",
            )
            log.info("✅  Synced %s -> %s", key, param_name)
            synced += 1
        except Exception as exc:  # noqa: BLE001
            log.error("❌  Failed to put %s: %s", param_name, exc)
            skipped += 1

    return synced, skipped


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Preview SSM sync without making changes")
    parser.add_argument("--sync", action="store_true", help="Sync values from .env to AWS SSM")
    parser.add_argument("--verify", action="store_true", help="Check existing SSM parameters")
    parser.add_argument("--prefix", default=SSM_PREFIX, help=f"SSM parameter prefix (default: {SSM_PREFIX})")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    region = settings.aws_region or "ap-southeast-1"
    ssm = boto3.client("ssm", region_name=region)

    if args.verify:
        log.info("Checking SSM parameters under %s in region %s...", args.prefix, region)
        status = verify_ssm_parameters(ssm, args.prefix)
        for key, exists in status.items():
            print(f"  [{'EXISTS' if exists else 'MISSING'}] {args.prefix}/{key}")
        return 0

    env_values = load_env_file(ROOT / ".env")
    if not env_values:
        log.warning(".env file not found or empty at %s", ROOT / ".env")

    dry_run = args.dry_run or (not args.sync)
    synced, skipped = sync_ssm_parameters(ssm, env_values, prefix=args.prefix, dry_run=dry_run)
    log.info("Finished: %d synced, %d skipped (dry_run=%s)", synced, skipped, dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())


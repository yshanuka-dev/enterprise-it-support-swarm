"""Upload raw transcript JSON files to S3: parallel, idempotent, validated.

Credentials come from the standard AWS chain (`aws configure`, SSO or env vars), never from code.

Usage:
    python -m incident_swarm.pipeline.s3_upload --dry-run
    python -m incident_swarm.pipeline.s3_upload --create-bucket
    python -m incident_swarm.pipeline.s3_upload            # normal run, safe to repeat
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from incident_swarm.config import ROOT, get_settings

log = logging.getLogger("s3_upload")

DEFAULT_SOURCE = ROOT / "data" / "raw"
DEFAULT_PREFIX = "transcripts/"
EXPECTED_FILES = 1110


def validate_file(path: Path) -> str | None:
    """Return an error message if the file is not a usable transcript, else None."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"unreadable or invalid JSON ({exc})"
    if not isinstance(doc, dict) or "transcript_id" not in doc or not doc.get("messages"):
        return "missing transcript_id or messages"
    return None


def list_existing(s3, bucket: str, prefix: str) -> dict[str, int]:
    """Return {key: size_in_bytes} for every object already under the prefix."""
    existing: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            existing[obj["Key"]] = obj["Size"]
    return existing


def ensure_bucket(s3, bucket: str, region: str) -> None:
    """Create a private bucket if it does not exist yet."""
    try:
        s3.head_bucket(Bucket=bucket)
        log.info("bucket %s already exists", bucket)
        return
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise  # e.g. 403: the name belongs to someone else
    kwargs = {"Bucket": bucket}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    log.info("created private bucket %s in %s", bucket, region)


def upload_one(s3, bucket: str, path: Path, key: str) -> None:
    """Upload a single file with JSON content type and server-side encryption."""
    s3.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={"ContentType": "application/json", "ServerSideEncryption": "AES256"},
    )


def main(argv: list[str] | None = None) -> int:
    """Validate local files, upload the missing ones in parallel, then verify the count."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--bucket", default=None, help="defaults to S3_BUCKET from .env")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--expected", type=int, default=EXPECTED_FILES, help="0 disables the check")
    parser.add_argument("--create-bucket", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="validate and report only")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    bucket = args.bucket or settings.s3_bucket
    if not bucket:
        log.error("no bucket: set S3_BUCKET in .env or pass --bucket")
        return 1

    files = sorted(args.source.glob("*.json"))
    log.info("found %d JSON files in %s", len(files), args.source)
    if args.expected and len(files) != args.expected:
        log.warning("expected %d files but found %d", args.expected, len(files))

    valid: list[Path] = []
    invalid = 0
    for path in files:
        error = validate_file(path)
        if error:
            invalid += 1
            log.warning("skipping %s: %s", path.name, error)
        else:
            valid.append(path)
    log.info("%d valid, %d invalid", len(valid), invalid)
    if args.dry_run:
        log.info("dry run: nothing uploaded")
        return 0

    s3 = boto3.client(
        "s3",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )
    try:
        if args.create_bucket:
            ensure_bucket(s3, bucket, settings.aws_region)
        existing = list_existing(s3, bucket, args.prefix)
    except (BotoCoreError, ClientError) as exc:
        log.error("cannot reach bucket %s: %s", bucket, exc)
        return 1

    todo = [
        (p, f"{args.prefix}{p.name}")
        for p in valid
        if existing.get(f"{args.prefix}{p.name}") != p.stat().st_size
    ]
    log.info("%d already uploaded, %d to upload", len(valid) - len(todo), len(todo))

    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(upload_one, s3, bucket, p, k): k for p, k in todo}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                future.result()
            except (BotoCoreError, ClientError, OSError) as exc:
                failures += 1
                log.error("failed %s: %s", futures[future], exc)
            if done % 100 == 0:
                log.info("uploaded %d / %d", done, len(todo))

    total = len(list_existing(s3, bucket, args.prefix))
    log.info("bucket now holds %d objects under %s (failures: %d)", total, args.prefix, failures)
    if failures or (args.expected and total != args.expected):
        log.error("verification failed: check the messages above and re-run (it is safe to repeat)")
        return 1
    log.info("done: S3 holds all expected transcripts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
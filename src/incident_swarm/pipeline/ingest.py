"""Day 4: distil every transcript from S3 (concurrent, retrying, resumable) and index into Qdrant.

Commands:
    python -m incident_swarm.pipeline.ingest distill --workers 4
    python -m incident_swarm.pipeline.ingest stats
    python -m incident_swarm.pipeline.ingest index
    python -m incident_swarm.pipeline.ingest search "Redis out of memory"
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3

from incident_swarm.config import ROOT, get_settings, load_model_config
from incident_swarm.memory.embeddings import get_embedder
from incident_swarm.memory.procedural import (
    COLLECTION,
    ensure_collection,
    get_qdrant_client,
    search,
    should_index,
    upsert_records,
)
from incident_swarm.pipeline import distill
from incident_swarm.pipeline.s3_upload import DEFAULT_PREFIX, list_existing

log = logging.getLogger("ingest")

DEFAULT_OUT = ROOT / "data" / "distilled_all.jsonl"
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
RETRYABLE_NAMES = ("ratelimit", "timeout", "connection", "unavailable", "overloaded")


def is_retryable(exc: Exception) -> bool:
    """True for rate limits, timeouts and 5xx errors from any provider SDK."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in RETRYABLE_STATUS:
        return True
    return any(token in type(exc).__name__.lower() for token in RETRYABLE_NAMES)


def with_retries(fn: Callable, attempts: int = 6, base: float = 1.0, cap: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep):
    """Call fn(); on retryable errors wait (exponential backoff plus jitter) and try again."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = min(cap, base * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)
            log.warning("retryable error (%s); attempt %d/%d, waiting %.1fs",
                        type(exc).__name__, attempt, attempts, delay)
            sleep(delay)

class RateLimiter:
    """Allow at most `rpm` calls per minute across all threads (0 disables the limit)."""

    def __init__(self, rpm: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block until this caller's turn."""
        if not self.interval:
            return
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            self._sleep(start - now)


def load_done_keys(path: Path) -> set[str]:
    """S3 keys already present in the output file (this is the checkpoint)."""
    if not path.exists():
        return set()
    return {json.loads(line)["s3_key"] for line in path.read_text(encoding="utf-8").splitlines() if line}


def read_records(path: Path) -> list[dict]:
    """Load all JSONL records."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def make_distill_fn() -> Callable[[dict], distill.Distilled]:
    """Return a function doc -> Distilled, using Gemini."""
    from google import genai

    settings = get_settings()
    model = load_model_config().tier_for("distillation").model
    client = genai.Client(api_key=settings.google_api_key, http_options={"api_version": "v1beta"})
    return lambda doc: distill.distill_one_gemini(client, model, doc)


def distill_all(s3, bucket: str, prefix: str, out: Path, workers: int, limit: int,
                distill_fn: Callable[[dict], distill.Distilled],
                sleep: Callable[[float], None] = time.sleep, rpm: float = 0,
                max_consecutive_failures: int = 8) -> tuple[int, int]:
    """Distil all transcripts not yet in `out`. Returns (succeeded, failed).

    rpm throttles requests per minute. If `max_consecutive_failures` records fail in a row
    (quota exhausted, blocked network, bad key) the run stops early instead of failing everything.
    """
    keys = sorted(list_existing(s3, bucket, prefix))
    if limit > 0:
        keys = keys[:limit]
    done_keys = load_done_keys(out)
    todo = [k for k in keys if k not in done_keys]
    log.info("%d transcripts in S3 scope, %d to do, %d workers, rpm limit %s",
             len(keys), len(todo), workers, rpm or "none")

    out.parent.mkdir(parents=True, exist_ok=True)
    error_path = out.with_suffix(".errors.jsonl")
    limiter = RateLimiter(rpm)
    ok = failed = consecutive = 0
    started = time.monotonic()

    def process(key: str) -> dict:
        doc = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())

        def call() -> distill.Distilled:
            limiter.wait()
            return distill_fn(doc)

        return distill.make_record(key, doc, with_retries(call, sleep=sleep))

    with out.open("a", encoding="utf-8") as ok_fh, error_path.open("a", encoding="utf-8") as err_fh, \
            ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, key): key for key in todo}
        for n, future in enumerate(as_completed(futures), start=1):
            key = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad transcript must not stop the run
                failed += 1
                consecutive += 1
                log.warning("failed %s: %s", key, exc)
                err_fh.write(json.dumps({"s3_key": key, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
                err_fh.flush()
                if consecutive >= max_consecutive_failures:
                    for pending in futures:
                        pending.cancel()
                    log.error("stopping: %d failures in a row (quota, network or key problem?). "
                              "Fix it and re-run; finished records are kept.", consecutive)
                    break
            else:
                ok += 1
                consecutive = 0
                ok_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                ok_fh.flush()
            if n % 25 == 0:
                rate = n / (time.monotonic() - started)
                log.info("progress %d / %d (%.1f/s, %d failed)", n, len(todo), rate, failed)
    return ok, failed


def print_stats(records: list[dict], min_confidence: float) -> None:
    """Summarise a distilled file: quality signals and how many records would be indexed."""
    total = len(records)
    indexable = sum(should_index(r, min_confidence) for r in records)
    matched = sum(bool(r.get("resolution_addresses_problem")) for r in records)
    print(f"records: {total}")
    print(f"concrete resolution: {sum(bool(r.get('has_concrete_resolution')) for r in records)}")
    print(f"problem matches resolution: {matched} ({matched / max(total, 1):.0%})")
    print(f"would be indexed (confidence >= {min_confidence}): {indexable}")
    print("by ticket_type:", dict(Counter(r.get("ticket_type") for r in records)))
    print("by root_cause_category:", dict(Counter(r.get("root_cause_category") for r in records)))
    confs = sorted(r.get("confidence", 0) for r in records)
    if confs:
        print(f"confidence min/median/max: {confs[0]:.2f} / {confs[len(confs) // 2]:.2f} / {confs[-1]:.2f}")


def index_records(records: list[dict], client, embedder, min_confidence: float,
                  batch_size: int = 64) -> dict[str, int]:
    """Embed and upsert the indexable records in batches (safe to repeat)."""
    ensure_collection(client, COLLECTION, embedder.dimensions)
    keep = [r for r in records if should_index(r, min_confidence)]
    for i in range(0, len(keep), batch_size):
        upsert_records(client, embedder, keep[i:i + batch_size])
        log.info("indexed %d / %d", min(i + batch_size, len(keep)), len(keep))
    return {"indexed": len(keep), "skipped": len(records) - len(keep)}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("distill", help="distil all transcripts from S3")
    d.add_argument("--workers", type=int, default=4)
    d.add_argument("--limit", type=int, default=0, help="0 means all")
    d.add_argument("--rpm", type=float, default=0, help="max requests per minute, 0 = unlimited")
    d.add_argument("--max-consecutive-failures", type=int, default=8)
    d.add_argument("--out", type=Path, default=DEFAULT_OUT)
    d.add_argument("--bucket", default=None)
    d.add_argument("--prefix", default=DEFAULT_PREFIX)

    for name in ("stats", "index"):
        p = sub.add_parser(name)
        p.add_argument("--file", type=Path, default=DEFAULT_OUT)

    s = sub.add_parser("search", help="query procedural memory")
    s.add_argument("query")
    s.add_argument("--k", type=int, default=3)
    s.add_argument("--ticket-type", default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_model_config()
    min_conf = cfg.limits["min_confidence_to_index"]

    if args.command == "distill":
        settings = get_settings()
        bucket = args.bucket or settings.s3_bucket
        if not bucket:
            log.error("set S3_BUCKET in .env")
            return 1
        s3 = boto3.client("s3", region_name=settings.aws_region)
        ok, failed = distill_all(s3, bucket, args.prefix, args.out, args.workers, args.limit,
                                 make_distill_fn(), rpm=args.rpm,
                                 max_consecutive_failures=args.max_consecutive_failures)
        log.info("finished: %d succeeded, %d failed. Re-run to retry failures.", ok, failed)
        return 0 if failed == 0 else 1

    if args.command == "stats":
        print_stats(read_records(args.file), min_conf)
        return 0

    embedder = get_embedder(cfg.embedding)
    client = get_qdrant_client()
    if args.command == "index":
        print(index_records(read_records(args.file), client, embedder, min_conf))
        return 0

    for hit in search(client, embedder, args.query, args.k, args.ticket_type):
        print(f"\nscore {hit['score']:.3f} | {hit['transcript_id']} [{hit['ticket_type']}] "
              f"matched={hit['problem_matched']}")
        print(f"  solves     : {hit['resolved_problem']}")
        print(f"  root cause : {hit['root_cause']}")
        print(f"  steps      : {'; '.join(hit['resolution_steps'])[:240]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

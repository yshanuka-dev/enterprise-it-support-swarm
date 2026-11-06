"""Distil raw support transcripts into structured procedural-memory records.

The transcripts are noisy: filler lines, false starts, duplicated messages, and often a
resolution that belongs to a different problem than the one the user reported. So each record is
anchored on the resolution message and carries a coherence flag.

Usage:
    # Gemini (default, free tier — rate-limited):
    python -m incident_swarm.pipeline.distill --limit 50 --out data/distilled_sample.jsonl

    # Groq (free tier, faster — recommended):
    python -m incident_swarm.pipeline.distill --provider groq --limit 50 --out data/distilled_sample.jsonl

    # Review quality of an existing output file (no LLM call):
    python -m incident_swarm.pipeline.distill --review data/distilled_sample.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Literal

import boto3
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from openai import OpenAI
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel, Field, field_validator

from incident_swarm.config import ROOT, get_settings, load_model_config
from incident_swarm.pipeline.s3_upload import DEFAULT_PREFIX, list_existing

log = logging.getLogger("distill")

# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------
MAX_RETRIES = 6
_DEFAULT_WAIT_S = 65.0          # fallback wait when retry-delay cannot be parsed

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.1-8b-instant"   # fast, free-tier friendly

RootCause = Literal[
    "configuration_drift",
    "resource_exhaustion",
    "software_bug",
    "human_error",
    "external_attack",
    "hardware_failure",
    "unknown",
]

SYSTEM_PROMPT = """You extract structured knowledge from IT support transcripts.

The transcripts are noisy on purpose:
- Many agent lines are generic filler ('checking our systems', 'bringing in a senior engineer').
- Agents often have FALSE STARTS: a first idea that failed ('my first idea did not work',
  'I was looking at the wrong thing'). Never treat these as the resolution.
- Closing lines like 'Verified. The service is back up' can be boilerplate even for hardware tickets.
- The same message may repeat.
- The agent's concrete resolution message is often about a DIFFERENT problem than the one the
  user first reported.

Rules:
1. user_problem: what the user actually reported, in one or two clear sentences. Ignore tone, slang and rudeness.
2. The resolution is the agent message that states a specific diagnosis and concrete actions. If there is none, set has_concrete_resolution to false.
3. resolved_problem: the problem the resolution message itself solves, inferred from that message (equal to user_problem when they match).
4. match_reasoning: one sentence comparing the SYSTEM or COMPONENT the user reported (service, host, device, tool, error) with the one the resolution fixed. Then set resolution_addresses_problem to true ONLY if they are the same system, or the resolution clearly explains the user's exact symptom. Sharing a vague symptom (slow, down, errors) is NOT enough. If the user names a specific system (for example the CI pipeline or Kubernetes nodes) and the resolution fixes a different one (for example an Elasticsearch task or auth pods), answer false. When unsure, answer false.
5. root_cause and resolution_steps must come ONLY from the resolution message, never from false starts.
6. root_cause_category: pick the closest category, or 'unknown'.
7. affected_service: a service or host named in the resolution, else null.
8. Never invent details that are not in the transcript. confidence (0 to 1) is how clearly the transcript supports your extraction: a specific, detailed resolution scores high, a vague one low. It is not about whether the problem matches.
9. In resolution_steps, replace employee IDs, ticket numbers, asset tags and personal names with placeholders such as <employee_id> or <ticket_id>.
"""


# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------
class Distilled(BaseModel):
    """Structured knowledge extracted from one transcript."""

    user_problem: str = Field(description="What the user reported, cleaned up")
    has_concrete_resolution: bool
    resolved_problem: str = Field(description="Problem the resolution message actually solves")
    match_reasoning: str = Field(
        description="One sentence comparing the reported system with the system the resolution fixed"
    )
    resolution_addresses_problem: bool
    root_cause: str = Field(description="Root cause stated in the resolution message")
    root_cause_category: RootCause
    resolution_steps: list[str]
    affected_service: str | None = None
    confidence: float

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------
def clean_messages(messages: list[dict]) -> list[dict]:
    """Drop repeated messages (same role and text) and normalise whitespace."""
    seen: set[tuple[str, str]] = set()
    cleaned: list[dict] = []
    for msg in messages:
        text = " ".join(msg["text"].split())
        key = (msg["role"], text.lower())
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"role": msg["role"], "text": text})
    return cleaned


def find_resolution_index(messages: list[dict]) -> int | None:
    """Heuristic: the longest agent message is usually the resolution. None if no agent turns."""
    agent = [(len(m["text"]), i) for i, m in enumerate(messages) if m["role"] == "agent"]
    return max(agent)[1] if agent else None


def build_prompt(doc: dict) -> str:
    """Render one transcript (metadata, numbered turns, resolution hint) for the LLM."""
    messages = clean_messages(doc["messages"])
    hint = find_resolution_index(messages)
    lines = [
        f"transcript_id: {doc.get('transcript_id')}",
        f"ticket_type label: {doc.get('ticket_type')}",
        f"channel: {doc.get('channel')}",
        "",
    ]
    for i, msg in enumerate(messages):
        lines.append(f"[{i}] {msg['role'].upper()}: {msg['text']}")
    if hint is not None:
        lines += ["", f"Hint: the longest agent message is turn [{hint}]; it is probably the resolution."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider-specific distillation functions
# ---------------------------------------------------------------------------
def distill_one_gemini(client, model: str, doc: dict) -> Distilled:
    """Call Gemini once and return the validated structured record."""
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=build_prompt(doc),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=Distilled,
            temperature=0.0,
        ),
    )
    if response.parsed is not None:
        return response.parsed
    return Distilled.model_validate_json(response.text)


distill_one = distill_one_gemini  # Backward compatibility alias for tests


def distill_one_groq(client: OpenAI, model: str, doc: dict) -> Distilled:
    """Call Groq (OpenAI-compatible) and return the validated structured record."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(doc)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    return Distilled.model_validate_json(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Retry wrapper (works for both providers)
# ---------------------------------------------------------------------------
def _parse_wait(exc: Exception) -> float:
    """Try to extract the suggested retry delay (seconds) from the error message."""
    match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(exc), re.IGNORECASE)
    return float(match.group(1)) + 3 if match else _DEFAULT_WAIT_S   # +3s safety buffer


def distill_with_retry(distill_fn, client, model: str, doc: dict) -> Distilled:
    """Call distill_fn with exponential backoff on 429 rate-limit errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return distill_fn(client, model, doc)
        except (genai_errors.APIError, OpenAIRateLimitError, Exception) as exc:
            msg = str(exc).lower()
            is_rate_limit = (
                "429" in str(exc)
                or "rate" in msg
                or "quota" in msg
                or "resource_exhausted" in msg
                or "too many requests" in msg
            )
            if not is_rate_limit or attempt == MAX_RETRIES:
                raise
            wait = _parse_wait(exc)
            log.warning(
                "rate limited (attempt %d/%d, provider=%s) — sleeping %.0fs before retry",
                attempt, MAX_RETRIES, distill_fn.__name__, wait,
            )
            time.sleep(wait)
    raise RuntimeError("unreachable")  # satisfy type checker


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------
def make_record(key: str, doc: dict, result: Distilled) -> dict:
    """Combine source metadata with the distilled fields (one JSONL line)."""
    meta = {
        name: doc.get(name)
        for name in (
            "transcript_id", "ticket_type", "channel", "user_tone", "agent_style",
            "satisfaction_score", "was_escalated", "resolution_time_minutes",
        )
    }
    return {"s3_key": key, **meta, **result.model_dump()}


def sample_keys(s3, bucket: str, prefix: str, limit: int, seed: int) -> list[str]:
    """Pick a reproducible random sample of object keys."""
    keys = sorted(list_existing(s3, bucket, prefix))
    random.Random(seed).shuffle(keys)
    return keys if limit <= 0 else keys[:limit]


def load_done_keys(path: Path) -> set[str]:
    """Return the S3 keys already distilled in an existing output file (for resuming)."""
    if not path.exists():
        return set()
    return {
        json.loads(line)["s3_key"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    }


def review(path: Path) -> None:
    """Print a compact summary and each record so you can check quality by eye."""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    ok = [r for r in records if "error" not in r]
    errors_n = len(records) - len(ok)
    coherent = sum(r["resolution_addresses_problem"] for r in ok)
    concrete = sum(r["has_concrete_resolution"] for r in ok)
    mean_conf = sum(r["confidence"] for r in ok) / max(len(ok), 1)
    print(f"{len(records)} records | errors {errors_n} | concrete resolution {concrete} | "
          f"resolution matches problem {coherent} | mean confidence {mean_conf:.2f}")
    for r in ok:
        flag = "MATCH   " if r["resolution_addresses_problem"] else "MISMATCH"
        print(f"\n{r['transcript_id']} [{r['ticket_type']}] {flag} conf={r['confidence']:.2f}")
        print(f"  user_problem    : {r['user_problem']}")
        print(f"  resolved_problem: {r['resolved_problem']}")
        print(f"  match_reasoning : {r['match_reasoning']}")
        print(f"  root_cause      : {r['root_cause']} ({r['root_cause_category']})")
        print(f"  steps           : {'; '.join(r['resolution_steps'])[:200]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Distil a sample of transcripts from S3, write structured JSONL, resume-safe."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["gemini", "groq"], default="gemini",
                        help="LLM provider to use (default: gemini)")
    parser.add_argument("--model", default=None,
                        help="Override model name (default: provider default)")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bucket", default=None)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "distilled_sample.jsonl")
    parser.add_argument("--review", type=Path, default=None, help="summarise an existing JSONL")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.review:
        review(args.review)
        return 0

    settings = get_settings()
    bucket = args.bucket or settings.s3_bucket
    if not bucket:
        log.error("set S3_BUCKET in .env or pass --bucket")
        return 1

    # -- Provider setup -------------------------------------------------------
    if args.provider == "groq":
        if not settings.groq_api_key:
            log.error("set GROQ_API_KEY in .env  (get a free key at console.groq.com)")
            return 1
        model = args.model or GROQ_DEFAULT_MODEL
        client = OpenAI(api_key=settings.groq_api_key, base_url=GROQ_BASE_URL)
        distill_fn = distill_one_groq
    else:  # gemini
        if not settings.google_api_key:
            log.error("set GOOGLE_API_KEY in .env")
            return 1
        model = args.model or load_model_config().tier_for("distillation").model
        client = genai.Client(api_key=settings.google_api_key)
        distill_fn = distill_one_gemini

    log.info("provider=%s  model=%s", args.provider, model)

    # -- S3 sampling ----------------------------------------------------------
    s3 = boto3.client("s3", region_name=settings.aws_region)
    keys = sample_keys(s3, bucket, args.prefix, args.limit, args.seed)
    log.info("distilling %d transcripts from s3://%s/%s", len(keys), bucket, args.prefix)

    # -- Resume-safe run ------------------------------------------------------
    args.out.parent.mkdir(parents=True, exist_ok=True)
    error_path = args.out.with_suffix(".errors.jsonl")
    done_keys = load_done_keys(args.out)
    todo = [k for k in keys if k not in done_keys]
    log.info("%d already distilled, %d to do", len(done_keys), len(todo))

    failures = 0
    with args.out.open("a", encoding="utf-8") as ok_fh, error_path.open("a", encoding="utf-8") as err_fh:
        for n, key in enumerate(todo, start=1):
            doc = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            try:
                record = make_record(key, doc, distill_with_retry(distill_fn, client, model, doc))
                ok_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                ok_fh.flush()
            except Exception as exc:  # noqa: BLE001 - one bad transcript must not stop the run
                failures += 1
                log.warning("failed %s: %s", key, exc)
                err_fh.write(json.dumps({"s3_key": key, "error": str(exc)}) + "\n")
                err_fh.flush()
            if n % 10 == 0:
                log.info("done %d / %d (failures so far: %d)", n, len(todo), failures)

    log.info("wrote %s (%d failures logged to %s). Re-run to retry failures.",
             args.out, failures, error_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

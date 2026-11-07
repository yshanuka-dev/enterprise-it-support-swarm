"""Day 5 tools: index runbooks and the FAQ cache, tag canonical incidents, ask, tune.

Commands:
    python -m incident_swarm.pipeline.knowledge index-runbooks
    python -m incident_swarm.pipeline.knowledge index-faq
    python -m incident_swarm.pipeline.knowledge tune
    python -m incident_swarm.pipeline.knowledge ask "How do I reset my VPN?"
    python -m incident_swarm.pipeline.knowledge tag-canonical --file data/distilled_all.jsonl
    python -m incident_swarm.pipeline.knowledge memory-search "Redis out of memory"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from incident_swarm.config import ROOT, load_model_config
from incident_swarm.memory import cag, canonical, procedural, runbooks
from incident_swarm.memory.embeddings import get_embedder

FAQ_PATH = ROOT / "config" / "faq.yaml"
RUNBOOK_DIR = ROOT / "runbooks"
DEFAULT_RECORDS = ROOT / "data" / "distilled_all.jsonl"


def tag_file(path: Path) -> dict[str, int]:
    """Add canonical fields to every record in a JSONL file (rewrites the file in place)."""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    tagged = [canonical.tag_record(r) for r in records]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in tagged), encoding="utf-8")
    unmapped = sum(1 for r in tagged if r.get("has_concrete_resolution") and not r["canonical_id"])
    return {"records": len(tagged), "tagged": sum(1 for r in tagged if r["canonical_id"]), "unmapped": unmapped}


def ask(query: str, client, embedder, threshold: float, min_score: float) -> None:
    """Show which path a question would take: CAG hit, or runbook retrieval."""
    entries, _ = cag.load_faq(FAQ_PATH)
    hit = cag.FaqCache(entries, client, embedder, threshold).lookup(query)
    if hit:
        print(f"CAG {hit.match} hit (score {hit.score:.2f}) -> {hit.entry.id}\n{hit.entry.answer}")
        return
    print("No CAG hit: this would go through the full agent graph.")
    found = runbooks.retrieve(client, embedder, query, 3, min_score)
    if not found:
        print("No runbook above the minimum score: the agent would say so and escalate.")
    for chunk in found:
        print(f"\n[{chunk['runbook_id']} > {chunk['section']}] score {chunk['score']:.2f}\n{chunk['text'][:300]}")


def tune(client, embedder, threshold: float) -> None:
    """Print scores for wanted hits and wanted misses, and suggest a threshold."""
    entries, doc = cag.load_faq(FAQ_PATH)
    cache = cag.FaqCache(entries, client, embedder, threshold)
    hit_scores: list[float] = []
    miss_scores: list[float] = []
    print("Should hit:")
    for question, expected in doc["test_hits"]:
        entry, score, _ = cache.best_match(question)
        ok = entry is not None and entry.id == expected
        hit_scores.append(score if ok else 0.0)
        print(f"  {score:.2f} {'ok ' if ok else 'WRONG'} {question} -> {entry.id if entry else None}")
    print("Should miss:")
    for question in doc["test_misses"]:
        entry, score, _ = cache.best_match(question)
        miss_scores.append(score)
        print(f"  {score:.2f} {question} -> {entry.id if entry else None}")
    print(f"\ncurrent threshold {threshold}; suggested {cag.suggest_threshold(hit_scores, miss_scores)}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index-runbooks")
    sub.add_parser("index-faq")
    sub.add_parser("tune")
    a = sub.add_parser("ask")
    a.add_argument("query")
    t = sub.add_parser("tag-canonical")
    t.add_argument("--file", type=Path, default=DEFAULT_RECORDS)
    m = sub.add_parser("memory-search")
    m.add_argument("query")
    m.add_argument("--k", type=int, default=3)
    args = parser.parse_args(argv)

    if args.command == "tag-canonical":
        print(tag_file(args.file))
        return 0

    cfg = load_model_config()
    embedder = get_embedder(cfg.embedding)
    client = procedural.get_qdrant_client()
    threshold = cfg.limits["cag_semantic_threshold"]
    min_score = cfg.limits["rag_min_score"]

    if args.command == "index-runbooks":
        print(f"indexed {runbooks.index_runbooks(client, embedder, RUNBOOK_DIR)} runbook sections")
    elif args.command == "index-faq":
        entries, _ = cag.load_faq(FAQ_PATH)
        print(f"indexed {cag.index_faq(client, embedder, entries)} FAQ questions")
    elif args.command == "tune":
        tune(client, embedder, threshold)
    elif args.command == "ask":
        ask(args.query, client, embedder, threshold, min_score)
    else:
        hits = procedural.search(client, embedder, args.query, k=args.k * 10)
        for hit in canonical.collapse_hits(hits, args.k):
            print(f"\nscore {hit['score']:.3f} | {hit.get('canonical_title') or hit['resolved_problem'][:70]} "
                  f"({hit.get('canonical_category')}) | {hit['similar_cases']} similar | runbook {hit.get('runbook_id')}")
            print(f"  steps: {'; '.join(hit['resolution_steps'])[:220]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

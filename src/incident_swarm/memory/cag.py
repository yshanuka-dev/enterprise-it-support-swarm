"""CAG fast path: a cache of verified answers for common Tier 1 questions.

Lookup order: exact match on the normalised question, then semantic match above a threshold,
otherwise None (the caller falls through to the full agent graph).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml
from qdrant_client import QdrantClient, models

from incident_swarm.memory.embeddings import Embedder
from incident_swarm.memory.procedural import ensure_collection

COLLECTION = "cag_faq"


@dataclass
class FaqEntry:
    """One cached answer with the question variants that map to it."""

    id : str
    topic: str
    answer: str
    runbook: str | None
    questions: list[str]


class CagHit(NamedTuple):
    """A cache hit: which entry, how it matched, and the similarity score."""

    entry: FaqEntry
    score: float
    match: str  # 'exact' or 'semantic'


def normalize(text: str) -> str:
    """Lowercase, drop punctuation and collapse whitespace."""
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def load_faq(path: Path) -> tuple[list[FaqEntry], dict]:
    """Load entries plus the raw document (which also holds test_hits and test_misses)."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = [
        FaqEntry(e["id"], e["topic"], e["answer"].strip(), e.get("runbook"), e["questions"])
        for e in doc["entries"]
    ]
    return entries, doc


def index_faq(client: QdrantClient, embedder: Embedder, entries: list[FaqEntry],
              collection: str = COLLECTION) -> int:
    """Embed every question variant and upsert it (idempotent). Returns the number of points."""
    pairs = [(e, q) for e in entries for q in e.questions]
    ensure_collection(client, collection, embedder.dimensions)
    vectors = embedder.embed([q for _, q in pairs])
    client.upsert(
        collection_name=collection,
        points=[
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"faq:{e.id}:{normalize(q)}")),
                vector=vec,
                payload={"entry_id": e.id, "question": q},
            )
            for (e, q), vec in zip(pairs, vectors, strict=True)
        ],
    )
    return len(pairs)


class FaqCache:
    """Exact then semantic lookup over FAQ entries."""

    def __init__(self, entries: list[FaqEntry], client: QdrantClient | None = None,
                 embedder: Embedder | None = None, threshold: float = 0.88,
                 collection: str = COLLECTION):
        self.entries = {e.id: e for e in entries}
        self.exact = {normalize(q): e for e in entries for q in e.questions}
        self.client = client
        self.embedder = embedder
        self.threshold = threshold
        self.collection = collection

    def best_match(self, query: str) -> tuple[FaqEntry | None, float, str]:
        """Best entry and score regardless of the threshold (used for tuning)."""
        hit = self.exact.get(normalize(query))
        if hit is not None:
            return hit, 1.0, "exact"
        if self.client is None or self.embedder is None:
            return None, 0.0, "none"
        result = self.client.query_points(
            collection_name=self.collection, query=self.embedder.embed([query])[0],
            limit=1, with_payload=True,
        )
        if not result.points:
            return None, 0.0, "none"
        top = result.points[0]
        return self.entries[top.payload["entry_id"]], top.score, "semantic"

    def lookup(self, query: str) -> CagHit | None:
        """Return a hit only if it is exact or its score reaches the threshold."""
        entry, score, match = self.best_match(query)
        if entry is not None and (match == "exact" or score >= self.threshold):
            return CagHit(entry, score, match)
        return None


def suggest_threshold(hit_scores: list[float], miss_scores: list[float]) -> float | None:
    """Midpoint between the weakest wanted hit and the strongest unwanted match, if separable."""
    if not hit_scores or not miss_scores or min(hit_scores) <= max(miss_scores):
        return None
    return round((min(hit_scores) + max(miss_scores)) / 2, 2)

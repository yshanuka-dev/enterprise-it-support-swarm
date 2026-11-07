"""Procedural memory in Qdrant: how past incidents were resolved.

Records are embedded by what the RESOLUTION solved (resolved_problem + root_cause), not by what
the user first typed, because many transcripts pair a user's problem with an unrelated fix.
The original user problem is kept in the payload together with a problem_matched flag.
"""

from __future__ import annotations

import uuid

from qdrant_client import QdrantClient, models

from incident_swarm.config import ROOT, Settings, get_settings
from incident_swarm.memory.embeddings import Embedder

COLLECTION = "procedural_memory"
KEYWORD_FIELDS = ("ticket_type", "root_cause_category", "channel")
PAYLOAD_FIELDS = (
    "transcript_id", "s3_key", "ticket_type", "channel", "user_problem", "resolved_problem",
    "root_cause", "root_cause_category", "resolution_steps", "affected_service", "confidence",
    "satisfaction_score", "resolution_time_minutes", "was_escalated",
    "canonical_id", "canonical_title", "canonical_category", "runbook_id",
)


def get_qdrant_client(settings: Settings | None = None, timeout: float = 60.0) -> QdrantClient:
    """Qdrant Cloud when QDRANT_URL is set, otherwise a local on-disk store under data/."""
    settings = settings or get_settings()
    if settings.qdrant_url:
        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=timeout)
    return QdrantClient(path=str(ROOT / "data" / "qdrant_local"), timeout=timeout)


def ensure_collection(client: QdrantClient, name: str, dimensions: int) -> None:
    """Create the collection (cosine distance) and filter indexes if they do not exist."""
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE),
    )
    for field in KEYWORD_FIELDS:
        client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)


def should_index(record: dict, min_confidence: float) -> bool:
    """Only index records with a concrete, confident, fully extracted resolution."""
    return bool(
        "error" not in record
        and record.get("has_concrete_resolution")
        and record.get("resolved_problem")
        and record.get("resolution_steps")
        and record.get("confidence", 0) >= min_confidence
    )


def embedding_text(record: dict) -> str:
    """Text that represents a record in vector space (resolution-anchored)."""
    parts = [record["resolved_problem"], f"Root cause: {record.get('root_cause', '')}"]
    if record.get("affected_service"):
        parts.append(f"Service: {record['affected_service']}")
    return ". ".join(parts)


def point_id(transcript_id: str) -> str:
    """Stable id per transcript so re-indexing overwrites instead of duplicating."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"transcript:{transcript_id}"))


def upsert_records(
    client: QdrantClient, embedder: Embedder, records: list[dict], collection: str = COLLECTION
) -> int:
    """Embed and upsert a batch of records; returns how many were written."""
    if not records:
        return 0
    vectors = embedder.embed([embedding_text(r) for r in records])
    points = [
        models.PointStruct(
            id=point_id(r["transcript_id"]),
            vector=vec,
            payload={
                **{k: r.get(k) for k in PAYLOAD_FIELDS},
                "problem_matched": bool(r.get("resolution_addresses_problem")),
            },
        )
        for r, vec in zip(records, vectors, strict=True)
    ]
    client.upsert(collection_name=collection, points=points)
    return len(points)


def search(
    client: QdrantClient,
    embedder: Embedder,
    query: str,
    k: int = 3,
    ticket_type: str | None = None,
    collection: str = COLLECTION,
) -> list[dict]:
    """Return the k most similar past resolutions as payload dicts with a score."""
    query_filter = None
    if ticket_type:
        query_filter = models.Filter(
            must=[models.FieldCondition(key="ticket_type", match=models.MatchValue(value=ticket_type))]
        )
    result = client.query_points(
        collection_name=collection,
        query=embedder.embed([query])[0],
        limit=k,
        query_filter=query_filter,
        with_payload=True,
    )
    return [{"score": hit.score, **hit.payload} for hit in result.points]

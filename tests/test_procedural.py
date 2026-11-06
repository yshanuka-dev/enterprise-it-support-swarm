"""Procedural memory tests using an in-memory Qdrant and a deterministic fake embedder."""

import hashlib
import math

import pytest
from qdrant_client import QdrantClient

from incident_swarm.config import Embedding
from incident_swarm.memory import embeddings, procedural


class FakeEmbedder:
    """Bag-of-words hashing into 64 dims: texts sharing words get similar vectors."""

    dimensions = 64

    def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * self.dimensions
            for word in text.lower().replace(".", " ").split():
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def _record(tid, solves, cause, ticket_type="critical_outage", conf=0.9, concrete=True, steps=("fix",)):
    return {
        "transcript_id": tid, "s3_key": f"transcripts/{tid}.json", "ticket_type": ticket_type,
        "channel": "chat", "user_problem": "user words", "resolved_problem": solves,
        "root_cause": cause, "root_cause_category": "resource_exhaustion",
        "resolution_steps": list(steps), "affected_service": None, "confidence": conf,
        "has_concrete_resolution": concrete, "resolution_addresses_problem": False,
    }


@pytest.fixture
def store():
    client = QdrantClient(":memory:")
    emb = FakeEmbedder()
    procedural.ensure_collection(client, procedural.COLLECTION, emb.dimensions)
    return client, emb


def test_should_index_filters_weak_records():
    assert procedural.should_index(_record("a", "x", "y"), 0.6)
    assert not procedural.should_index(_record("a", "x", "y", conf=0.3), 0.6)
    assert not procedural.should_index(_record("a", "x", "y", concrete=False), 0.6)
    assert not procedural.should_index(_record("a", "x", "y", steps=()), 0.6)
    assert not procedural.should_index({"error": "boom"}, 0.6)


def test_upsert_is_idempotent_and_search_ranks_relevant_first(store):
    client, emb = store
    records = [
        _record("T1", "Redis ran out of memory due to eviction policy", "noeviction policy set"),
        _record("T2", "SMTP connection pool exhausted dropping emails", "pool max 10"),
        _record("T3", "Stuck Elasticsearch reindex task slowed search", "single thread reindex"),
    ]
    procedural.upsert_records(client, emb, records)
    procedural.upsert_records(client, emb, records)  # again: must not duplicate
    assert client.count(procedural.COLLECTION).count == 3
    hits = procedural.search(client, emb, "redis out of memory eviction", k=2)
    assert hits[0]["transcript_id"] == "T1"
    assert hits[0]["problem_matched"] is False
    assert hits[0]["score"] >= hits[1]["score"]


def test_search_can_filter_by_ticket_type(store):
    client, emb = store
    procedural.upsert_records(client, emb, [
        _record("T1", "Redis memory eviction", "policy", ticket_type="critical_outage"),
        _record("T2", "Redis memory eviction cache", "policy", ticket_type="service_degradation"),
    ])
    hits = procedural.search(client, emb, "redis memory", k=5, ticket_type="service_degradation")
    assert [h["transcript_id"] for h in hits] == ["T2"]


def test_embedding_text_uses_resolution_not_user_words():
    text = procedural.embedding_text(_record("T1", "Redis OOM", "noeviction"))
    assert "Redis OOM" in text and "user words" not in text


def test_get_embedder_rejects_unknown_provider():
    with pytest.raises(ValueError):
        embeddings.get_embedder(Embedding(provider="nope", model="m", dimensions=3))

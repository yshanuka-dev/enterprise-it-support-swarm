"""Tests for runbook retrieval, the CAG cache, canonical incident ids and the shipped content files."""

import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from tests.test_procedural import FakeEmbedder

from incident_swarm.memory import cag, canonical, procedural, runbooks

ROOT = Path(__file__).parents[1]
RUNBOOK_FILES = sorted((ROOT / "runbooks").glob("*.md"))


@pytest.fixture
def client():
    return QdrantClient(":memory:")


# ---- shipped content ----

@pytest.mark.parametrize("path", RUNBOOK_FILES, ids=lambda p: p.name)
def test_every_runbook_parses_with_id_and_sections(path):
    chunks = runbooks.parse_runbook(path)
    assert len(chunks) >= 3
    assert all(c["runbook_id"].startswith("RB-") for c in chunks)


def test_runbook_ids_are_unique_and_cover_canonical_references():
    ids = [runbooks.parse_runbook(p)[0]["runbook_id"] for p in RUNBOOK_FILES]
    assert len(ids) == len(set(ids)) and len(ids) >= 5
    referenced = {r.runbook_id for r in canonical.RULES if r.runbook_id}
    assert referenced <= set(ids)


def test_faq_file_is_well_formed():
    entries, doc = cag.load_faq(ROOT / "config" / "faq.yaml")
    assert len({e.id for e in entries}) == len(entries) >= 3
    assert all(e.questions and e.answer for e in entries)
    ids = {e.id for e in entries}
    assert all(expected in ids for _, expected in doc["test_hits"])


# ---- runbooks ----

def test_runbook_retrieval_cites_id_and_section_and_respects_min_score(client):
    emb = FakeEmbedder()
    assert runbooks.index_runbooks(client, emb, ROOT / "runbooks") > 10
    hits = runbooks.retrieve(client, emb, "redis out of memory noeviction policy", k=3, min_score=0.1)
    assert hits[0]["runbook_id"] == "RB-REDIS-OOM" and hits[0]["section"]
    assert runbooks.retrieve(client, emb, "redis out of memory", min_score=0.999) == []
    count = client.count(runbooks.COLLECTION).count
    runbooks.index_runbooks(client, emb, ROOT / "runbooks")  # idempotent
    assert client.count(runbooks.COLLECTION).count == count


def test_parse_runbook_requires_id(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("# No id\n\n## Symptoms\n- x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        runbooks.parse_runbook(bad)


# ---- CAG ----

def _cache(client, threshold=0.5):
    entries, _ = cag.load_faq(ROOT / "config" / "faq.yaml")
    emb = FakeEmbedder()
    cag.index_faq(client, emb, entries)
    return cag.FaqCache(entries, client, emb, threshold)


def test_cag_exact_match_ignores_case_and_punctuation(client):
    hit = _cache(client).lookup("how do i reset my vpn")
    assert hit.match == "exact" and hit.entry.id == "faq-vpn-reset" and hit.score == 1.0


def test_cag_semantic_hit_and_miss(client):
    cache = _cache(client, threshold=0.6)  # fake embedder counts stop words, so use a stricter cut-off
    assert cache.lookup("my vpn connection keeps failing").entry.id == "faq-vpn-reset"
    assert cache.lookup("Why is the checkout API so slow today?") is None


def test_suggest_threshold():
    assert cag.suggest_threshold([0.9, 0.8], [0.5, 0.6]) == 0.7
    assert cag.suggest_threshold([0.6], [0.7]) is None


# ---- canonical ids ----

def test_canonical_rules_order_and_unknown():
    assert canonical.canonical_id("auth-api pods OOMKilled; use a Redis-backed cache") == "auth_oom"
    assert canonical.canonical_id("CONFIRMED: Redis prod-cache-01 OOM") == "redis_oom"
    assert canonical.canonical_id("Okay so, um, cleared the stale VPN session on RADIUS") == "vpn_stale_session"
    assert canonical.canonical_id("something unrelated") is None
    assert canonical.canonical_id(None) is None


def test_tag_record_and_collapse_hits():
    tagged = canonical.tag_record({"resolution_message": "Redis OOM, fixed eviction"})
    assert tagged["canonical_id"] == "redis_oom" and tagged["runbook_id"] == "RB-REDIS-OOM"
    assert canonical.tag_record({"resolution_message": "???"})["canonical_id"] is None
    hits = [
        {"score": 0.9, "transcript_id": "A", "canonical_id": "redis_oom"},
        {"score": 0.8, "transcript_id": "B", "canonical_id": "redis_oom"},
        {"score": 0.7, "transcript_id": "C", "canonical_id": "smtp_pool"},
    ]
    out = canonical.collapse_hits(hits, k=5)
    assert [h["transcript_id"] for h in out] == ["A", "C"] and out[0]["similar_cases"] == 2


def test_tag_file_and_search_end_to_end(tmp_path, client):
    records = [
        {"transcript_id": "T1", "resolution_message": "Redis OOM noeviction fix", "resolved_problem": "Redis out of memory",
         "root_cause": "noeviction policy", "resolution_steps": ["set allkeys-lru"], "confidence": 0.9,
         "has_concrete_resolution": True},
        {"transcript_id": "T2", "resolution_message": "Redis OOM noeviction fix again", "resolved_problem": "Redis out of memory",
         "root_cause": "noeviction policy", "resolution_steps": ["set allkeys-lru"], "confidence": 0.9,
         "has_concrete_resolution": True},
    ]
    path = tmp_path / "r.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    from incident_swarm.pipeline import knowledge

    assert knowledge.tag_file(path) == {"records": 2, "tagged": 2, "unmapped": 0}
    tagged = [json.loads(line) for line in path.read_text().splitlines()]
    emb = FakeEmbedder()
    procedural.ensure_collection(client, procedural.COLLECTION, emb.dimensions)
    procedural.upsert_records(client, emb, tagged)
    hits = procedural.search(client, emb, "redis out of memory", k=10)
    collapsed = canonical.collapse_hits(hits, 3)
    assert len(collapsed) == 1 and collapsed[0]["similar_cases"] == 2
    assert collapsed[0]["canonical_title"].startswith("Redis")

"""Runbook knowledge base: split markdown runbooks into sections, index them in Qdrant, retrieve with citations.

Each `##` section becomes one chunk, so an answer can cite the runbook id and section heading.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

from incident_swarm.memory.embeddings import Embedder
from incident_swarm.memory.procedural import ensure_collection

COLLECTION = "runbooks"
ID_PATTERN = re.compile(r"^\*\*Runbook ID:\*\*\s*(\S+)", re.MULTILINE)


def parse_runbook(path: Path) -> list[dict]:
    """Split one runbook file into chunks (one per `##` section) with citation metadata."""
    text = path.read_text(encoding="utf-8")
    id_match = ID_PATTERN.search(text)
    if not id_match:
        raise ValueError(f"{path.name}: missing '**Runbook ID:**' line")
    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    runbook_id = id_match.group(1)

    chunks: list[dict] = []
    for block in re.split(r"^##\s+", text, flags=re.MULTILINE)[1:]:
        heading, _, body = block.partition("\n")
        body = body.strip()
        if not body:
            continue
        chunks.append({
            "runbook_id": runbook_id,
            "title": title,
            "section": heading.strip(),
            "source": path.name,
            "text": body,
        })
    return chunks


def load_runbooks(directory: Path) -> list[dict]:
    """Parse every .md runbook in a directory."""
    chunks: list[dict] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(parse_runbook(path))
    return chunks


def _chunk_id(chunk: dict) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"runbook:{chunk['runbook_id']}:{chunk['section']}"))


def index_runbooks(client: QdrantClient, embedder: Embedder, directory: Path,
                   collection: str = COLLECTION) -> int:
    """Embed and upsert all runbook chunks (idempotent). Returns the number of chunks."""
    chunks = load_runbooks(directory)
    if not chunks:
        return 0
    ensure_collection(client, collection, embedder.dimensions)
    vectors = embedder.embed([f"{c['title']}. {c['section']}. {c['text']}" for c in chunks])
    client.upsert(
        collection_name=collection,
        points=[
            models.PointStruct(id=_chunk_id(c), vector=vec, payload=c)
            for c, vec in zip(chunks, vectors, strict=True)
        ],
    )
    return len(chunks)


def retrieve(client: QdrantClient, embedder: Embedder, query: str, k: int = 3,
             min_score: float = 0.35, collection: str = COLLECTION) -> list[dict]:
    """Return up to k chunks scoring at least min_score. An empty list means 'no runbook found'."""
    result = client.query_points(
        collection_name=collection, query=embedder.embed([query])[0], limit=k, with_payload=True
    )
    return [{"score": hit.score, **hit.payload} for hit in result.points if hit.score >= min_score]

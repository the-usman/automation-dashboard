"""RAG grounding + vector dual-write (Phase 4 + the Qdrant half of Phase 6).

Mirrors n8n: embed a seed query → search the client's Qdrant KB → format the
retrieved context for the trend prompt. Upserts go to the trends_signals /
content_ideas collections. All guarded — if Qdrant isn't configured we return an
empty context and skip upserts (matching the "RAG Enabled?" / Set-Empty branch).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from .config import get_settings
from .llm import get_embeddings

log = logging.getLogger(__name__)

try:  # optional dependency
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams
except Exception:  # noqa: BLE001
    QdrantClient = None  # type: ignore


def _client():
    settings = get_settings()
    if not settings.qdrant_url or QdrantClient is None:
        return None
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)


def retrieve_context(client: dict, config: dict) -> str:
    """Embed a seed query from the niche and pull related prior knowledge."""
    if not client.get("rag_enabled"):
        return ""
    qc = _client()
    namespace = client.get("qdrant_kb_namespace")
    if not qc or not namespace:
        return ""
    try:
        seed = f"{client.get('niche', '')} {' '.join(client.get('niche_keywords') or [])}".strip()
        vector = get_embeddings().embed_query(seed or client.get("client_name", "trends"))
        hits = qc.search(collection_name=namespace, query_vector=vector, limit=5)
        lines = [f"- {h.payload.get('text', '')}" for h in hits if h.payload]
        if not lines:
            return ""
        return "Relevant prior knowledge for this client:\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        log.warning("RAG retrieve failed: %s", e)
        return ""


def _ensure_collection(qc, name: str, size: int) -> None:
    try:
        existing = {c.name for c in qc.get_collections().collections}
        if name not in existing:
            qc.create_collection(name, vectors_config=VectorParams(size=size, distance=Distance.COSINE))
    except Exception as e:  # noqa: BLE001
        log.warning("Qdrant ensure_collection(%s) failed: %s", name, e)


def upsert_records(collection: str, rows: list[dict[str, Any]], text_key: str) -> None:
    """Embed each row's text and upsert into a Qdrant collection."""
    qc = _client()
    if not qc or not rows:
        return
    try:
        embeddings = get_embeddings()
        texts = [str(r.get(text_key, "")) for r in rows]
        vectors = embeddings.embed_documents(texts)
        _ensure_collection(qc, collection, len(vectors[0]))
        points = [
            PointStruct(id=row.get("qdrant_point_id") or str(uuid.uuid4()), vector=vec, payload=row)
            for row, vec in zip(rows, vectors)
        ]
        qc.upsert(collection_name=collection, points=points)
        log.info("Qdrant upserted %d points into %s", len(points), collection)
    except Exception as e:  # noqa: BLE001
        log.warning("Qdrant upsert(%s) failed: %s", collection, e)

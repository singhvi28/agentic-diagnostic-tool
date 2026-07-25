"""Qdrant client for semantic log / traceback search."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from diagnostic_engine.config import Settings, get_settings

# Fixed dim for the built-in hash embedding (no external model required for MVP).
EMBED_DIM = 384


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic bag-of-hashes embedding for offline/dev use.

    Replace with a real embedding model (OpenAI / sentence-transformers) in production.
    """
    vec = [0.0] * dim
    tokens = text.lower().split()
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    # L2 normalize
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


class QdrantMemory:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: QdrantClient | None = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            kwargs: dict[str, Any] = {"url": self.settings.qdrant_url}
            if self.settings.qdrant_api_key:
                kwargs["api_key"] = self.settings.qdrant_api_key
            self._client = QdrantClient(**kwargs)
        return self._client

    def ensure_collection(self) -> None:
        name = self.settings.qdrant_collection
        collections = {c.name for c in self.client.get_collections().collections}
        if name not in collections:
            self.client.create_collection(
                collection_name=name,
                vectors_config=qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE),
            )

    def upsert_log(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        point_id: str | None = None,
    ) -> str:
        self.ensure_collection()
        pid = point_id or str(uuid.uuid4())
        payload = {"text": text, **(metadata or {})}
        self.client.upsert(
            collection_name=self.settings.qdrant_collection,
            points=[
                qm.PointStruct(
                    id=pid,
                    vector=hash_embed(text),
                    payload=payload,
                )
            ],
        )
        return pid

    def query_similar(self, text: str, top_k: int = 3) -> list[dict[str, Any]]:
        self.ensure_collection()
        hits = self.client.search(
            collection_name=self.settings.qdrant_collection,
            query_vector=hash_embed(text),
            limit=top_k,
            with_payload=True,
        )
        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                {
                    "id": str(hit.id),
                    "score": hit.score,
                    "metadata": payload,
                }
            )
        return results

    def ping(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False

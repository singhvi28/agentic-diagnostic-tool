"""Text embedding helpers for pgvector memory."""

from __future__ import annotations

import hashlib
from typing import Sequence

from diagnostic_engine.config import Settings, get_settings


def hash_embed(text: str, dim: int = 384) -> list[float]:
    """Deterministic bag-of-hashes embedding for offline/dev use."""
    vec = [0.0] * dim
    tokens = text.lower().split()
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def openai_embed(text: str, *, dim: int, api_key: str) -> list[float]:
    """Embed via OpenAI text-embedding-3-small, truncated/padded to `dim`."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
        dimensions=min(dim, 1536),
    )
    vector = list(response.data[0].embedding)
    if len(vector) < dim:
        vector = vector + [0.0] * (dim - len(vector))
    return vector[:dim]


def embed_text(text: str, settings: Settings | None = None) -> list[float]:
    settings = settings or get_settings()
    dim = settings.embedding_dim
    if settings.embedding_provider == "openai" and settings.openai_api_key:
        return openai_embed(text, dim=dim, api_key=settings.openai_api_key)
    return hash_embed(text, dim=dim)


def as_pgvector(values: Sequence[float]) -> list[float]:
    return list(values)

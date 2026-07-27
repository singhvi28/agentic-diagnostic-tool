"""Text / graph embedding helpers for pgvector + Neo4j FastRP memory."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any, Sequence

from diagnostic_engine.config import Settings, get_settings

if TYPE_CHECKING:
    from diagnostic_engine.memory.neo4j_client import Neo4jMemory

_FUNC_FROM_TRACE_RE = re.compile(r",\s*in\s+(?P<func>\S+)")


def hash_embed(text: str, dim: int = 384) -> list[float]:
    """Deterministic bag-of-hashes embedding for offline/dev use (last resort)."""
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


def gemini_embed(text: str, *, dim: int, api_key: str) -> list[float]:
    """Embed via Gemini text-embedding-004, truncated/padded to `dim`."""
    from google import genai

    client = genai.Client(api_key=api_key)
    response = client.models.embed_content(
        model="text-embedding-004",
        contents=text,
    )
    values: list[float]
    if hasattr(response, "embeddings") and response.embeddings:
        values = list(response.embeddings[0].values)
    elif hasattr(response, "embedding") and response.embedding is not None:
        values = list(response.embedding.values)
    else:
        raise RuntimeError(f"Unexpected Gemini embed response: {response!r}")
    if len(values) < dim:
        values = values + [0.0] * (dim - len(values))
    return values[:dim]


def _pad_or_truncate(vector: list[float], dim: int) -> list[float]:
    if len(vector) == dim:
        return vector
    if len(vector) < dim:
        return vector + [0.0] * (dim - len(vector))
    return vector[:dim]


def function_name_from_text(text: str) -> str | None:
    """Best-effort failing function name from a traceback or free text."""
    matches = list(_FUNC_FROM_TRACE_RE.finditer(text))
    if matches:
        return matches[-1].group("func")
    stripped = text.strip()
    if stripped.isidentifier():
        return stripped
    return None


class FastRPEmbedder:
    """Embeddings from graph structure via Neo4j GDS FastRP."""

    def __init__(self, neo4j_memory: Neo4jMemory | None = None) -> None:
        from diagnostic_engine.memory.neo4j_client import Neo4jMemory as _Neo

        self._neo = neo4j_memory or _Neo()
        self._owns_neo = neo4j_memory is None

    def close(self) -> None:
        if self._owns_neo:
            self._neo.close()

    def embed(self, function_name: str) -> list[float] | None:
        return self._neo.get_function_graph_embedding(function_name)

    def embed_text(self, text: str, *, dim: int) -> list[float] | None:
        """Map text → failing Function FastRP vector, padded to ``dim``."""
        name = function_name_from_text(text)
        if not name:
            return None
        emb = self.embed(name)
        if emb is None:
            return None
        return _pad_or_truncate(list(emb), dim)

    def top_k_similar(self, function_name: str, k: int = 5) -> list[dict[str, Any]]:
        """KNN via cosine similarity over Function.graph_embedding."""
        return self._neo.similar_functions_by_fastrp(function_name, k=k)


def _should_use_api_embed(settings: Settings) -> str | None:
    """Return 'openai' | 'gemini' when provider + key are usable."""
    if settings.embedding_provider == "openai" and settings.openai_api_key:
        return "openai"
    if settings.embedding_provider == "gemini" and settings.gemini_api_key:
        return "gemini"
    if settings.embedding_provider == "auto":
        if settings.openai_api_key:
            return "openai"
        if settings.gemini_api_key:
            return "gemini"
    return None


def _try_fastrp_embed(text: str, dim: int) -> list[float] | None:
    try:
        from diagnostic_engine.memory.neo4j_client import Neo4jMemory

        neo = Neo4jMemory()
        try:
            if not neo.ping():
                return None
            embedder = FastRPEmbedder(neo)
            return embedder.embed_text(text, dim=dim)
        finally:
            neo.close()
    except Exception:
        return None


def embed_text(text: str, settings: Settings | None = None) -> list[float]:
    """Embed text for pgvector.

    Cascade: OpenAI/Gemini (when configured + keyed) → Neo4j FastRP structural
    embeddings → deterministic hash (last resort).
    """
    settings = settings or get_settings()
    dim = settings.embedding_dim

    api = _should_use_api_embed(settings)
    if api == "openai":
        return openai_embed(text, dim=dim, api_key=settings.openai_api_key)
    if api == "gemini":
        return gemini_embed(text, dim=dim, api_key=settings.gemini_api_key)

    # Explicit fastrp/hash/auto, or openai/gemini without keys → prefer FastRP
    fastrp_vec = _try_fastrp_embed(text, dim)
    if fastrp_vec is not None:
        return fastrp_vec

    return hash_embed(text, dim=dim)


def as_pgvector(values: Sequence[float]) -> list[float]:
    return list(values)

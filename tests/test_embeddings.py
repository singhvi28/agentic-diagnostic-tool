"""Unit tests for embedding cascade and FastRP helpers (no live APIs)."""

from __future__ import annotations

from unittest.mock import MagicMock

from diagnostic_engine.config import Settings
from diagnostic_engine.memory.embeddings import (
    FastRPEmbedder,
    function_name_from_text,
    hash_embed,
    embed_text,
)


def test_function_name_from_traceback():
    text = (
        'File "main.py", line 60, in checkout\n'
        "    time.sleep(0.05)\n"
        "KeyboardInterrupt: blocked\n"
    )
    assert function_name_from_text(text) == "checkout"


def test_hash_embed_deterministic():
    a = hash_embed("KeyError in checkout", dim=32)
    b = hash_embed("KeyError in checkout", dim=32)
    assert a == b
    assert len(a) == 32


def test_fastrp_embedder_reads_neo_property():
    neo = MagicMock()
    neo.get_function_graph_embedding.return_value = [0.1, 0.2, 0.3]
    neo.similar_functions_by_fastrp.return_value = [{"name": "get_order", "sim": 0.9}]
    emb = FastRPEmbedder(neo)
    assert emb.embed("checkout") == [0.1, 0.2, 0.3]
    padded = emb.embed_text(
        'File "main.py", line 1, in checkout\nErr\n',
        dim=5,
    )
    assert padded == [0.1, 0.2, 0.3, 0.0, 0.0]
    assert emb.top_k_similar("checkout", k=1)[0]["name"] == "get_order"


def test_embed_text_falls_back_to_fastrp_before_hash(monkeypatch):
    settings = Settings(
        embedding_provider="hash",
        openai_api_key="",
        gemini_api_key="",
        embedding_dim=4,
    )
    monkeypatch.setattr(
        "diagnostic_engine.memory.embeddings._try_fastrp_embed",
        lambda text, dim: [1.0, 0.0, 0.0, 0.0],
    )
    vec = embed_text("ignored", settings)
    assert vec == [1.0, 0.0, 0.0, 0.0]


def test_embed_text_uses_hash_when_fastrp_missing(monkeypatch):
    settings = Settings(
        embedding_provider="hash",
        openai_api_key="",
        gemini_api_key="",
        embedding_dim=8,
    )
    monkeypatch.setattr(
        "diagnostic_engine.memory.embeddings._try_fastrp_embed",
        lambda text, dim: None,
    )
    vec = embed_text("hello world", settings)
    assert vec == hash_embed("hello world", dim=8)

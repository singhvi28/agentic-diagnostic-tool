"""Postgres + pgvector client for semantic log / traceback search."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text

from diagnostic_engine.config import Settings, get_settings
from diagnostic_engine.db.models import LogEmbedding
from diagnostic_engine.db.session import get_engine, session_scope
from diagnostic_engine.memory.embeddings import embed_text


class PgVectorMemory:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def ensure_schema(self) -> None:
        """Ensure vector extension exists (tables come from Alembic)."""
        engine = get_engine(self.settings)
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    def upsert_log(
        self,
        text_value: str,
        metadata: dict[str, Any] | None = None,
        point_id: str | None = None,
    ) -> str:
        self.ensure_schema()
        pid = uuid.UUID(point_id) if point_id else uuid.uuid4()
        vector = embed_text(text_value, self.settings)
        with session_scope(self.settings) as session:
            existing = session.get(LogEmbedding, pid)
            if existing is None:
                session.add(
                    LogEmbedding(
                        id=pid,
                        text=text_value,
                        embedding=vector,
                        metadata_=metadata or {},
                    )
                )
            else:
                existing.text = text_value
                existing.embedding = vector
                existing.metadata_ = metadata or {}
        return str(pid)

    def query_similar(
        self,
        text_value: str,
        top_k: int = 3,
        *,
        exception_type: str | None = None,
        function_name: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        vector = embed_text(text_value, self.settings)
        with session_scope(self.settings) as session:
            distance = LogEmbedding.embedding.cosine_distance(vector)
            stmt = select(
                LogEmbedding.id,
                LogEmbedding.text,
                LogEmbedding.metadata_,
                (1 - distance).label("score"),
            )
            if exception_type:
                stmt = stmt.where(
                    LogEmbedding.metadata_["exception_type"].as_string() == exception_type
                )
            if function_name:
                stmt = stmt.where(
                    LogEmbedding.metadata_["function_name"].as_string() == function_name
                )
            stmt = stmt.order_by(distance).limit(top_k)
            rows = session.execute(stmt).all()
            return [
                {
                    "id": str(row.id),
                    "score": float(row.score) if row.score is not None else 0.0,
                    "metadata": {**(row.metadata_ or {}), "text": row.text},
                }
                for row in rows
            ]

    def ping(self) -> bool:
        try:
            engine = get_engine(self.settings)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

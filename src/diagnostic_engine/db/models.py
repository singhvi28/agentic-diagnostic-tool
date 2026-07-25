"""SQLAlchemy models for sessions, patches, and pgvector log embeddings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from diagnostic_engine.config import get_settings


class Base(DeclarativeBase):
    pass


class DiagnosticSession(Base):
    __tablename__ = "diagnostic_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    raw_log: Mapped[str] = mapped_column(Text, default="")
    exception_type: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    failing_function: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    root_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[SessionEvent]] = relationship(back_populates="session", cascade="all, delete-orphan")
    patches: Mapped[list[Patch]] = relationship(back_populates="session", cascade="all, delete-orphan")


class SessionEvent(Base):
    __tablename__ = "session_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diagnostic_sessions.id", ondelete="CASCADE"), index=True
    )
    node_name: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[DiagnosticSession] = relationship(back_populates="events")


class Patch(Base):
    __tablename__ = "patches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diagnostic_sessions.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    files: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    test_code: Mapped[str] = mapped_column(Text, default="")
    test_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[DiagnosticSession] = relationship(back_populates="patches")


def _embedding_dim() -> int:
    return get_settings().embedding_dim


class LogEmbedding(Base):
    __tablename__ = "log_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    text: Mapped[str] = mapped_column(Text)
    # Dimension is fixed at migration time; default 384 matches Settings.embedding_dim
    embedding: Mapped[list[float]] = mapped_column(Vector(384))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

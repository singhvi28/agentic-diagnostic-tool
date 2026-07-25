"""Session / patch / event repository."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from diagnostic_engine.db.models import DiagnosticSession, Patch, SessionEvent
from diagnostic_engine.db.session import session_scope


def _truncate_payload(payload: dict[str, Any], max_chars: int = 8000) -> dict[str, Any]:
    raw = json.dumps(payload, default=str)
    if len(raw) <= max_chars:
        return payload
    return {"_truncated": True, "preview": raw[:max_chars]}


def create_session(
    raw_log: str,
    *,
    exception_type: str | None = None,
    failing_function: str | None = None,
    db: Session | None = None,
) -> uuid.UUID:
    def _create(session: Session) -> uuid.UUID:
        row = DiagnosticSession(
            raw_log=raw_log,
            exception_type=exception_type,
            failing_function=failing_function,
            status="running",
        )
        session.add(row)
        session.flush()
        return row.id

    if db is not None:
        return _create(db)
    with session_scope() as session:
        return _create(session)


def append_event(
    session_id: str | uuid.UUID,
    node_name: str,
    payload: dict[str, Any],
    *,
    db: Session | None = None,
) -> uuid.UUID:
    sid = uuid.UUID(str(session_id))

    def _append(session: Session) -> uuid.UUID:
        row = SessionEvent(
            session_id=sid,
            node_name=node_name,
            payload=_truncate_payload(payload),
        )
        session.add(row)
        session.flush()
        return row.id

    if db is not None:
        return _append(db)
    with session_scope() as session:
        return _append(session)


def finish_session(
    session_id: str | uuid.UUID,
    status: str,
    *,
    root_cause: str | None = None,
    retry_count: int | None = None,
    db: Session | None = None,
) -> None:
    sid = uuid.UUID(str(session_id))

    def _finish(session: Session) -> None:
        row = session.get(DiagnosticSession, sid)
        if row is None:
            return
        row.status = status
        row.root_cause = root_cause
        if retry_count is not None:
            row.retry_count = retry_count
        row.finished_at = datetime.now(timezone.utc)

    if db is not None:
        _finish(db)
        return
    with session_scope() as session:
        _finish(session)


def add_patch(
    session_id: str | uuid.UUID,
    attempt: int,
    files: dict[str, Any],
    test_code: str,
    *,
    test_passed: bool | None = None,
    applied: bool = False,
    db: Session | None = None,
) -> uuid.UUID:
    sid = uuid.UUID(str(session_id))

    def _add(session: Session) -> uuid.UUID:
        row = Patch(
            session_id=sid,
            attempt=attempt,
            files=files,
            test_code=test_code,
            test_passed=test_passed,
            applied=applied,
        )
        session.add(row)
        session.flush()
        return row.id

    if db is not None:
        return _add(db)
    with session_scope() as session:
        return _add(session)


def update_latest_patch_test_result(
    session_id: str | uuid.UUID,
    test_passed: bool,
    *,
    db: Session | None = None,
) -> None:
    sid = uuid.UUID(str(session_id))

    def _update(session: Session) -> None:
        stmt = (
            select(Patch)
            .where(Patch.session_id == sid)
            .order_by(Patch.attempt.desc(), Patch.created_at.desc())
            .limit(1)
        )
        row = session.scalars(stmt).first()
        if row is not None:
            row.test_passed = test_passed

    if db is not None:
        _update(db)
        return
    with session_scope() as session:
        _update(session)


def mark_latest_patch_applied(
    session_id: str | uuid.UUID,
    *,
    db: Session | None = None,
) -> Optional[uuid.UUID]:
    sid = uuid.UUID(str(session_id))

    def _mark(session: Session) -> Optional[uuid.UUID]:
        stmt = (
            select(Patch)
            .where(Patch.session_id == sid)
            .order_by(Patch.attempt.desc(), Patch.created_at.desc())
            .limit(1)
        )
        row = session.scalars(stmt).first()
        if row is None:
            return None
        row.applied = True
        return row.id

    if db is not None:
        return _mark(db)
    with session_scope() as session:
        return _mark(session)


def list_patches(session_id: str | uuid.UUID) -> list[dict[str, Any]]:
    sid = uuid.UUID(str(session_id))
    with session_scope() as session:
        rows = session.scalars(
            select(Patch).where(Patch.session_id == sid).order_by(Patch.attempt.asc())
        ).all()
        return [
            {
                "id": str(r.id),
                "attempt": r.attempt,
                "files": r.files,
                "test_code": r.test_code,
                "test_passed": r.test_passed,
                "applied": r.applied,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def get_session(session_id: str | uuid.UUID) -> dict[str, Any] | None:
    sid = uuid.UUID(str(session_id))
    with session_scope() as session:
        row = session.scalars(
            select(DiagnosticSession)
            .where(DiagnosticSession.id == sid)
            .options(selectinload(DiagnosticSession.events), selectinload(DiagnosticSession.patches))
        ).first()
        if row is None:
            return None
        return {
            "id": str(row.id),
            "status": row.status,
            "raw_log": row.raw_log,
            "exception_type": row.exception_type,
            "failing_function": row.failing_function,
            "root_cause": row.root_cause,
            "retry_count": row.retry_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "events": [
                {
                    "id": str(e.id),
                    "node_name": e.node_name,
                    "payload": e.payload,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in sorted(row.events, key=lambda x: x.created_at or datetime.min.replace(tzinfo=timezone.utc))
            ],
            "patches": [
                {
                    "id": str(p.id),
                    "attempt": p.attempt,
                    "files": p.files,
                    "test_code": p.test_code,
                    "test_passed": p.test_passed,
                    "applied": p.applied,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in sorted(row.patches, key=lambda x: x.attempt)
            ],
        }


def list_recent_sessions(limit: int = 20) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(DiagnosticSession)
            .order_by(DiagnosticSession.created_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": str(r.id),
                "status": r.status,
                "exception_type": r.exception_type,
                "failing_function": r.failing_function,
                "retry_count": r.retry_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]

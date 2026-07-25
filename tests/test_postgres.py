"""Postgres + pgvector + session repository tests (requires local Postgres)."""

from __future__ import annotations

import os
import uuid

import pytest

# Ensure settings pick up compose-mapped port before imports that cache settings
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://diagnostic:diagnostic@localhost:5433/diagnostic",
)

from diagnostic_engine.config import get_settings
from diagnostic_engine.db import repository as repo
from diagnostic_engine.db.session import reset_engine
from diagnostic_engine.memory.pgvector_client import PgVectorMemory


@pytest.fixture(autouse=True)
def _reset_settings_and_engine():
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def require_postgres():
    pg = PgVectorMemory()
    if not pg.ping():
        pytest.skip("Postgres not available on DATABASE_URL")
    return pg


def test_pgvector_upsert_and_query(require_postgres):
    pg = require_postgres
    text = f"UniqueKeyError in checkout session={uuid.uuid4()}"
    pid = pg.upsert_log(
        text,
        metadata={"exception_type": "KeyError", "function_name": "checkout"},
    )
    assert pid
    hits = pg.query_similar(text, top_k=3)
    assert hits
    assert any(text[:20] in (h.get("metadata") or {}).get("text", "") for h in hits)


def test_session_and_patch_repository(require_postgres):
    sid = repo.create_session(
        "Traceback...",
        exception_type="IndexError",
        failing_function="get_order",
    )
    repo.append_event(sid, "parse_log", {"ok": True})
    patch_id = repo.add_patch(
        sid,
        attempt=1,
        files={"main.py": "# fixed"},
        test_code="def test_ok():\n    assert True\n",
        test_passed=None,
    )
    assert patch_id
    repo.update_latest_patch_test_result(sid, True)
    repo.finish_session(sid, "passed", root_cause="IndexError without HTTPException", retry_count=1)

    data = repo.get_session(sid)
    assert data is not None
    assert data["status"] == "passed"
    assert len(data["events"]) >= 1
    assert len(data["patches"]) == 1
    assert data["patches"][0]["test_passed"] is True

    recent = repo.list_recent_sessions(5)
    assert any(r["id"] == str(sid) for r in recent)

"""Shared fixtures for unit and integration tests."""

from __future__ import annotations

import os

import pytest

# Compose-mapped Postgres before settings are cached
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://diagnostic:diagnostic@localhost:5433/diagnostic",
)

from diagnostic_engine.agent.mcp_client import DiagnosticMcpClient, set_mcp_client
from diagnostic_engine.config import Settings, get_settings
from diagnostic_engine.db.session import reset_engine
from diagnostic_engine.memory.neo4j_client import Neo4jMemory
from diagnostic_engine.memory.pgvector_client import PgVectorMemory
from diagnostic_engine.mcp.server import mcp as diagnostic_mcp

SAMPLE_CHECKOUT_TRACEBACK = (
    'Traceback (most recent call last):\n'
    '  File "examples/target_app/main.py", line 60, in checkout\n'
    "    time.sleep(0.05)\n"
    "KeyboardInterrupt: event loop blocked during checkout\n"
)


@pytest.fixture(autouse=True)
def _reset_runtime_singletons():
    get_settings.cache_clear()
    reset_engine()
    set_mcp_client(None)
    yield
    set_mcp_client(None)
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def require_neo4j():
    neo = Neo4jMemory()
    try:
        if not neo.ping():
            pytest.skip("Neo4j not available")
        yield neo
    finally:
        neo.close()


@pytest.fixture
def require_postgres():
    pg = PgVectorMemory()
    if not pg.ping():
        pytest.skip("Postgres not available on DATABASE_URL")
    return pg


@pytest.fixture
def require_graph_stores(require_neo4j, require_postgres):
    return {"neo4j": require_neo4j, "postgres": require_postgres}


@pytest.fixture
def inprocess_mcp(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "inprocess")
    get_settings.cache_clear()
    settings = Settings(mcp_transport="inprocess")
    with DiagnosticMcpClient.from_settings(settings, server=diagnostic_mcp) as client:
        yield client


@pytest.fixture
def sample_checkout_traceback():
    return SAMPLE_CHECKOUT_TRACEBACK
